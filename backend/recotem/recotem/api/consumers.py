import asyncio
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db import models

from recotem.api.models import ParameterTuningJob, TaskLog

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 60  # seconds
# Maximum number of historical log entries sent to late-joining clients.
LATE_JOIN_BUFFER_LIMIT = 500


class AuthenticatedConsumerMixin:
    """Mixin that rejects unauthenticated WebSocket connections."""

    async def check_auth(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return False
        return True

    @database_sync_to_async
    def has_job_access(self, job_id: int) -> bool:
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            return False
        return ParameterTuningJob.objects.filter(id=job_id).filter(
            models.Q(data__project__owner_id=user.id)
            | models.Q(data__project__owner__isnull=True)
        ).exists()


class HeartbeatMixin:
    """Mixin that sends periodic ping frames to keep the connection alive."""

    _heartbeat_task: asyncio.Task | None = None

    def start_heartbeat(self):
        self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())

    def stop_heartbeat(self):
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _heartbeat_loop(self):
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self.send(text_data=json.dumps({"type": "ping"}))
        except asyncio.CancelledError:
            pass

    async def receive(self, text_data=None, bytes_data=None):
        """Handle incoming messages, responding to pong."""
        if text_data:
            try:
                data = json.loads(text_data)
                if data.get("type") == "pong":
                    return  # heartbeat response, ignore
            except (json.JSONDecodeError, TypeError):
                pass


class JobStatusConsumer(
    AuthenticatedConsumerMixin, HeartbeatMixin, AsyncWebsocketConsumer
):
    """WebSocket consumer for real-time job status updates."""

    @database_sync_to_async
    def _get_current_job_status(self, job_id: int) -> dict | None:
        """Fetch current job status for late-joining clients."""
        try:
            job = ParameterTuningJob.objects.get(id=job_id)
            return {
                "type": "status_update",
                "status": job.status.lower(),
                "data": {
                    "best_score": job.best_score,
                    "buffered": True,
                },
            }
        except ParameterTuningJob.DoesNotExist:
            return None

    async def connect(self):
        if not await self.check_auth():
            return
        self.job_id = int(self.scope["url_route"]["kwargs"]["job_id"])
        if not await self.has_job_access(self.job_id):
            await self.close(code=4403)
            return
        self.group_name = f"job_{self.job_id}_status"
        self._seq = 0

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        self.start_heartbeat()

        # Send current job status to late-joining clients.
        current_status = await self._get_current_job_status(self.job_id)
        if current_status is not None:
            current_status["seq"] = self._seq
            self._seq += 1
            await self.send(text_data=json.dumps(current_status))

    async def disconnect(self, close_code):
        self.stop_heartbeat()
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def job_status_update(self, event):
        """Handle job status update messages from the channel layer."""
        msg = {
            "type": "status_update",
            "status": event["status"],
            "data": event.get("data", {}),
            "seq": self._seq,
        }
        self._seq += 1
        await self.send(text_data=json.dumps(msg))


class TaskLogConsumer(
    AuthenticatedConsumerMixin, HeartbeatMixin, AsyncWebsocketConsumer
):
    """WebSocket consumer for streaming task log messages."""

    @database_sync_to_async
    def _get_existing_logs(self, job_id: int) -> list[dict]:
        """Fetch existing TaskLog entries for a job, ordered by creation time.

        Returns up to LATE_JOIN_BUFFER_LIMIT entries so late-joining clients
        can see the history of a running (or completed) job.
        """
        logs = (
            TaskLog.objects.filter(task__tuning_job_link__job_id=job_id)
            .order_by("ins_datetime")
            .values_list("contents", "ins_datetime")[:LATE_JOIN_BUFFER_LIMIT]
        )
        return [
            {
                "type": "log",
                "message": contents,
                "timestamp": ins_dt.isoformat() if ins_dt else "",
                "buffered": True,
            }
            for contents, ins_dt in logs
        ]

    async def connect(self):
        if not await self.check_auth():
            return
        self.job_id = int(self.scope["url_route"]["kwargs"]["job_id"])
        if not await self.has_job_access(self.job_id):
            await self.close(code=4403)
            return
        self.group_name = f"job_{self.job_id}_logs"
        self._seq = 0

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        self.start_heartbeat()

        # Send existing log entries to late-joining clients.
        try:
            existing_logs = await self._get_existing_logs(self.job_id)
            for log_entry in existing_logs:
                log_entry["seq"] = self._seq
                self._seq += 1
                await self.send(text_data=json.dumps(log_entry))
        except Exception:
            logger.exception(
                "Failed to send log buffer for job %d", self.job_id
            )

    async def disconnect(self, close_code):
        self.stop_heartbeat()
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def task_log_message(self, event):
        """Handle task log messages from the channel layer."""
        msg = {
            "type": "log",
            "message": event["message"],
            "timestamp": event.get("timestamp", ""),
            "seq": self._seq,
        }
        self._seq += 1
        await self.send(text_data=json.dumps(msg))
