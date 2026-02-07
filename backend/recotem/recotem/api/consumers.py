import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth.models import AnonymousUser
from django.db import models

from recotem.api.models import ParameterTuningJob


class AuthenticatedConsumerMixin:
    """Mixin that rejects unauthenticated WebSocket connections."""

    async def check_auth(self):
        user = self.scope.get("user", AnonymousUser())
        if isinstance(user, AnonymousUser) or not user.is_authenticated:
            await self.close(code=4401)
            return False
        return True

    @database_sync_to_async
    def has_job_access(self, job_id: int) -> bool:
        user = self.scope.get("user", AnonymousUser())
        if isinstance(user, AnonymousUser) or not user.is_authenticated:
            return False
        return ParameterTuningJob.objects.filter(id=job_id).filter(
            models.Q(data__project__owner_id=user.id)
            | models.Q(data__project__owner__isnull=True)
        ).exists()


class JobStatusConsumer(AuthenticatedConsumerMixin, AsyncWebsocketConsumer):
    """WebSocket consumer for real-time job status updates."""

    async def connect(self):
        if not await self.check_auth():
            return
        self.job_id = int(self.scope["url_route"]["kwargs"]["job_id"])
        if not await self.has_job_access(self.job_id):
            await self.close(code=4403)
            return
        self.group_name = f"job_{self.job_id}_status"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def job_status_update(self, event):
        """Handle job status update messages from the channel layer."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "status_update",
                    "status": event["status"],
                    "data": event.get("data", {}),
                }
            )
        )


class TaskLogConsumer(AuthenticatedConsumerMixin, AsyncWebsocketConsumer):
    """WebSocket consumer for streaming task log messages."""

    async def connect(self):
        if not await self.check_auth():
            return
        self.job_id = int(self.scope["url_route"]["kwargs"]["job_id"])
        if not await self.has_job_access(self.job_id):
            await self.close(code=4403)
            return
        self.group_name = f"job_{self.job_id}_logs"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def task_log_message(self, event):
        """Handle task log messages from the channel layer."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "log",
                    "message": event["message"],
                    "timestamp": event.get("timestamp", ""),
                }
            )
        )
