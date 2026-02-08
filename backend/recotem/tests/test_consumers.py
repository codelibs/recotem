
import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django_celery_results.models import TaskResult

from recotem.api.consumers import JobStatusConsumer, TaskLogConsumer
from recotem.api.models import (
    EvaluationConfig,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TaskAndParameterJobLink,
    TaskLog,
    TrainingData,
)

User = get_user_model()


@database_sync_to_async
def _create_user(username="testuser", password="testpass"):
    return User.objects.create_user(username=username, password=password)


@database_sync_to_async
def _create_job(owner):
    """Create a full ParameterTuningJob chain for testing."""
    project = Project.objects.create(
        name="test_project",
        owner=owner,
        user_column="user_id",
        item_column="item_id",
    )
    split = SplitConfig.objects.create(name="test_split")
    evaluation = EvaluationConfig.objects.create(name="test_eval")
    # Create a minimal TrainingData (no actual file needed for consumer tests)
    data = TrainingData.objects.create(project=project, file="dummy.csv")
    job = ParameterTuningJob.objects.create(
        data=data,
        split=split,
        evaluation=evaluation,
        n_tasks_parallel=1,
        n_trials=1,
        memory_budget=4096,
    )
    return job


def _make_communicator(consumer_class, job_id, user=None):
    """Build a WebsocketCommunicator with the given user in scope."""
    communicator = WebsocketCommunicator(
        consumer_class.as_asgi(),
        f"/ws/job/{job_id}/status/"
        if consumer_class is JobStatusConsumer
        else f"/ws/job/{job_id}/logs/",
    )
    communicator.scope["url_route"] = {"kwargs": {"job_id": str(job_id)}}
    communicator.scope["user"] = user if user is not None else AnonymousUser()
    return communicator


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_unauthenticated_connection_rejected():
    """Unauthenticated users should be rejected with code 4401."""
    communicator = _make_communicator(JobStatusConsumer, 99999)
    connected, code = await communicator.connect()
    # The consumer calls close(4401) before accept, so connect returns False
    assert not connected
    assert code == 4401
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_other_user_job_rejected():
    """A user should not be able to connect to another user's job (code 4403)."""
    user_a = await _create_user("owner_a", "pass")
    user_b = await _create_user("other_b", "pass")
    job = await _create_job(user_a)

    communicator = _make_communicator(JobStatusConsumer, job.id, user=user_b)
    connected, code = await communicator.connect()
    assert not connected
    assert code == 4403
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_unauthenticated_log_connection_rejected():
    """Unauthenticated users should be rejected from TaskLogConsumer with code 4401."""
    communicator = _make_communicator(TaskLogConsumer, 99999)
    connected, code = await communicator.connect()
    assert not connected
    assert code == 4401
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_other_user_log_connection_rejected():
    """A user should not be able to connect to another user's job logs (code 4403)."""
    user_a = await _create_user("log_owner_a", "pass")
    user_b = await _create_user("log_other_b", "pass")
    job = await _create_job(user_a)

    communicator = _make_communicator(TaskLogConsumer, job.id, user=user_b)
    connected, code = await communicator.connect()
    assert not connected
    assert code == 4403
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_owner_can_connect_and_receive_status():
    """The job owner should connect successfully and receive status updates."""
    user = await _create_user("status_owner", "pass")
    job = await _create_job(user)

    communicator = _make_communicator(JobStatusConsumer, job.id, user=user)
    connected, _ = await communicator.connect()
    assert connected

    # First message is the buffered current status sent on connect
    buffered = await communicator.receive_json_from(timeout=5)
    assert buffered["type"] == "status_update"
    assert buffered["data"]["buffered"] is True
    assert buffered["seq"] == 0

    # Send a status update via the channel layer
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        f"job_{job.id}_status",
        {"type": "job_status_update", "status": "running", "data": {"progress": 50}},
    )

    response = await communicator.receive_json_from(timeout=5)
    assert response["type"] == "status_update"
    assert response["status"] == "running"
    assert response["data"]["progress"] == 50
    assert response["seq"] == 1

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_owner_can_connect_and_receive_logs():
    """The job owner should connect to the log consumer and receive messages."""
    user = await _create_user("log_owner", "pass")
    job = await _create_job(user)

    communicator = _make_communicator(TaskLogConsumer, job.id, user=user)
    connected, _ = await communicator.connect()
    assert connected

    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        f"job_{job.id}_logs",
        {"type": "task_log_message", "message": "Trial 1 complete"},
    )

    response = await communicator.receive_json_from(timeout=5)
    assert response["type"] == "log"
    assert response["message"] == "Trial 1 complete"
    assert response["seq"] == 0

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_heartbeat_ping_sent_after_interval():
    """Connected consumers should send heartbeat pings periodically."""
    import asyncio
    from unittest.mock import patch

    user = await _create_user("heartbeat_user", "pass")
    job = await _create_job(user)

    communicator = _make_communicator(JobStatusConsumer, job.id, user=user)

    # Patch HEARTBEAT_INTERVAL to a short value for testing
    with patch("recotem.api.consumers.HEARTBEAT_INTERVAL", 0.1):
        connected, _ = await communicator.connect()
        assert connected

        # Consume the buffered status sent on connect
        buffered = await communicator.receive_json_from(timeout=5)
        assert buffered["data"]["buffered"] is True

        # Wait for heartbeat
        await asyncio.sleep(0.3)

        # Should have received at least one ping
        response = await communicator.receive_json_from(timeout=2)
        assert response["type"] == "ping"

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_pong_response_is_ignored():
    """Sending a pong message should not cause errors or disconnection."""
    user = await _create_user("pong_user", "pass")
    job = await _create_job(user)

    communicator = _make_communicator(JobStatusConsumer, job.id, user=user)
    connected, _ = await communicator.connect()
    assert connected

    # Consume the buffered status sent on connect
    buffered = await communicator.receive_json_from(timeout=5)
    assert buffered["data"]["buffered"] is True

    # Send a pong message (as client would in response to a ping)
    await communicator.send_json_to({"type": "pong"})

    # Connection should remain open — send a status update to verify
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        f"job_{job.id}_status",
        {"type": "job_status_update", "status": "running", "data": {}},
    )

    response = await communicator.receive_json_from(timeout=5)
    assert response["type"] == "status_update"

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_invalid_json_message_does_not_crash():
    """Sending invalid JSON should not crash the consumer."""
    user = await _create_user("badjson_user", "pass")
    job = await _create_job(user)

    communicator = _make_communicator(JobStatusConsumer, job.id, user=user)
    connected, _ = await communicator.connect()
    assert connected

    # Consume the buffered status sent on connect
    buffered = await communicator.receive_json_from(timeout=5)
    assert buffered["data"]["buffered"] is True

    # Send invalid JSON text
    await communicator.send_to(text_data="not valid json {{{")

    # Connection should remain open — verify by sending valid data
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        f"job_{job.id}_status",
        {"type": "job_status_update", "status": "completed", "data": {}},
    )

    response = await communicator.receive_json_from(timeout=5)
    assert response["type"] == "status_update"

    await communicator.disconnect()


@database_sync_to_async
def _create_task_logs_for_job(job, messages):
    """Create TaskLog entries linked to a job via TaskAndParameterJobLink."""
    task_result = TaskResult.objects.create(task_id=f"test-task-{job.id}")
    TaskAndParameterJobLink.objects.create(job=job, task=task_result)
    for msg in messages:
        TaskLog.objects.create(task=task_result, contents=msg)
    return task_result


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_late_joining_log_consumer_receives_existing_logs():
    """A late-joining client should receive existing TaskLog entries on connect."""
    user = await _create_user("latejoin_log_user", "pass")
    job = await _create_job(user)

    # Create some existing log entries before connecting
    messages = ["Trial 0 started", "Trial 0 complete", "Trial 1 started"]
    await _create_task_logs_for_job(job, messages)

    communicator = _make_communicator(TaskLogConsumer, job.id, user=user)
    connected, _ = await communicator.connect()
    assert connected

    # Should receive buffered log entries in order with incrementing seq
    for i, expected_msg in enumerate(messages):
        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] == "log"
        assert response["message"] == expected_msg
        assert response["buffered"] is True
        assert "timestamp" in response
        assert response["seq"] == i

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_late_joining_log_consumer_no_logs():
    """A late-joining client with no existing logs should not receive any buffered messages."""
    user = await _create_user("latejoin_nolog_user", "pass")
    job = await _create_job(user)

    communicator = _make_communicator(TaskLogConsumer, job.id, user=user)
    connected, _ = await communicator.connect()
    assert connected

    # Send a new live log message to verify the connection works
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        f"job_{job.id}_logs",
        {"type": "task_log_message", "message": "Live message"},
    )

    # The first message received should be the live one (no buffered messages)
    response = await communicator.receive_json_from(timeout=5)
    assert response["type"] == "log"
    assert response["message"] == "Live message"
    assert "buffered" not in response
    assert response["seq"] == 0

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_late_joining_status_consumer_receives_current_status():
    """A late-joining client should receive the current job status on connect."""
    user = await _create_user("latejoin_status_user", "pass")
    job = await _create_job(user)

    communicator = _make_communicator(JobStatusConsumer, job.id, user=user)
    connected, _ = await communicator.connect()
    assert connected

    # Should receive the initial buffered status (job starts as PENDING)
    response = await communicator.receive_json_from(timeout=5)
    assert response["type"] == "status_update"
    assert response["status"] == "pending"
    assert response["data"]["buffered"] is True
    assert response["seq"] == 0

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_late_joining_status_consumer_running_job():
    """A late-joining client connecting to a RUNNING job should see 'running' status."""
    user = await _create_user("latejoin_running_user", "pass")
    job = await _create_job(user)

    # Update the job to RUNNING status before connecting
    @database_sync_to_async
    def set_running():
        job.status = ParameterTuningJob.Status.RUNNING
        job.save(update_fields=["status"])

    await set_running()

    communicator = _make_communicator(JobStatusConsumer, job.id, user=user)
    connected, _ = await communicator.connect()
    assert connected

    response = await communicator.receive_json_from(timeout=5)
    assert response["type"] == "status_update"
    assert response["status"] == "running"
    assert response["data"]["buffered"] is True
    assert response["seq"] == 0

    await communicator.disconnect()
