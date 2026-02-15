# WebSocket Protocol Specification

## Overview

Recotem uses WebSocket connections to deliver real-time updates from background Celery tasks to connected clients. The WebSocket layer is built on Django Channels with a Redis channel layer (db1). Two consumer types are provided: `JobStatusConsumer` for job status updates and `TaskLogConsumer` for streaming task log messages.

## Connection Endpoints

| Endpoint | Consumer | Description |
|---|---|---|
| `ws://host:8000/ws/job/{job_id}/status/` | `JobStatusConsumer` | Real-time job status updates |
| `ws://host:8000/ws/job/{job_id}/logs/` | `TaskLogConsumer` | Streaming task log messages |

Routing is defined in `backend/recotem/recotem/api/routing.py`:
```python
websocket_urlpatterns = [
    re_path(r"^ws/job/(?P<job_id>\d+)/status/$", JobStatusConsumer.as_asgi()),
    re_path(r"^ws/job/(?P<job_id>\d+)/logs/$", TaskLogConsumer.as_asgi()),
]
```

## Authentication

### JWT via Query Parameter

Browsers cannot send custom HTTP headers on WebSocket upgrade requests. Instead, JWT access tokens are passed as a `?token=<access_token>` query parameter.

```
ws://host:8000/ws/job/42/status/?token=eyJhbGciOiJIUzI1NiIs...
```

### Authentication Flow

```
Client                          nginx                      Daphne (ASGI)
  |                               |                            |
  |  GET /ws/job/42/status/       |                            |
  |  ?token=<jwt>                 |                            |
  |  Upgrade: websocket           |                            |
  |------------------------------>|                            |
  |                               |  proxy_pass (ws upgrade)   |
  |                               |--------------------------->|
  |                               |                            |
  |                               |          JwtAuthMiddleware |
  |                               |          +-----------------+
  |                               |          | Parse ?token    |
  |                               |          | Validate JWT    |
  |                               |          | Load User       |
  |                               |          | Set scope[user] |
  |                               |          +-----------------+
  |                               |                            |
  |                               |          Consumer.connect()|
  |                               |          +-----------------+
  |                               |          | check_auth()    |
  |                               |          | has_job_access()|
  |                               |          | Accept or Close |
  |                               |          +-----------------+
  |                               |                            |
  |<--------------------------------------------- 101 / 4401  |
```

### JwtAuthMiddleware

Defined in `backend/recotem/recotem/api/middleware.py`. Intercepts every WebSocket connection before it reaches the consumer:

1. Extracts `token` from query string parameters
2. Validates the JWT access token via `rest_framework_simplejwt.tokens.AccessToken`
3. Loads the corresponding Django user
4. Sets `scope["user"]` for downstream consumers
5. Falls back to `AnonymousUser` if no token or validation fails

### AuthenticatedConsumerMixin

Both consumers use `AuthenticatedConsumerMixin` which provides:

- **`check_auth()`**: Rejects unauthenticated users with close code `4401`
- **`has_job_access(job_id)`**: Verifies the user owns the job's project (or the project is unowned/legacy). Returns `False` and closes with code `4403` if access is denied.

```python
# Access check query:
ParameterTuningJob.objects.filter(id=job_id).filter(
    Q(data__project__owner_id=user.id) | Q(data__project__owner__isnull=True)
).exists()
```

### Close Codes

| Code | Meaning |
|---|---|
| `4401` | Unauthenticated -- no valid JWT token |
| `4403` | Forbidden -- user does not have access to this job |

## Heartbeat Mechanism

Both consumers use `HeartbeatMixin` to keep connections alive.

### Configuration

- **Interval**: 60 seconds (`HEARTBEAT_INTERVAL`)
- **Direction**: Server sends `ping`, client responds with `pong`

### Protocol

```
Server                              Client
  |                                    |
  |  {"type": "ping"}                  |
  |----------------------------------->|
  |                                    |
  |  {"type": "pong"}                  |
  |<-----------------------------------|
  |                                    |
  |  ... 60 seconds ...                |
  |                                    |
  |  {"type": "ping"}                  |
  |----------------------------------->|
```

### Implementation

- The heartbeat loop runs as an `asyncio` task, started on connection and cancelled on disconnect.
- Client `pong` messages are consumed by the `receive()` method and silently ignored (no processing).
- The heartbeat keeps the WebSocket connection open through proxies and load balancers that may have idle timeouts.
- nginx is configured with `proxy_read_timeout 300s` for WebSocket connections.

## Consumer 1: JobStatusConsumer

### Purpose

Delivers real-time status updates for `ParameterTuningJob` instances. Used by the frontend to show job progress without polling.

### Channel Group

Group name: `job_{job_id}_status`

### Connection Sequence

1. Validate JWT authentication
2. Check job access permissions
3. Join channel group `job_{job_id}_status`
4. Accept WebSocket connection
5. Start heartbeat
6. Send current job status snapshot (late-join support)

### Late-Join Buffer

On connection, the consumer queries the current `ParameterTuningJob` status from the database and sends it as an initial message. This ensures clients that connect after a job has started (or completed) receive the current state:

```json
{
  "type": "status_update",
  "status": "running",
  "data": {
    "best_score": 0.85,
    "buffered": true
  },
  "seq": 0
}
```

The `buffered: true` flag indicates this is historical data, not a live event.

### Message Format: status_update

Sent by the consumer to clients when a status change occurs.

```json
{
  "type": "status_update",
  "status": "<status>",
  "data": { ... },
  "seq": 0
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"status_update"` |
| `status` | string | Job status: `"pending"`, `"running"`, `"completed"`, `"error"` |
| `data` | object | Status-specific payload |
| `seq` | integer | Monotonically increasing sequence number per connection |

**Status-specific data payloads**:

| Status | Data Fields |
|---|---|
| `running` | `{}` (empty) |
| `completed` | `{"best_score": <float>}` |
| `error` | `{"error": "<message>"}` |

### Internal Channel Event: job_status_update

Celery tasks send status updates to the channel group via:

```python
channel_layer.group_send(
    f"job_{job_id}_status",
    {"type": "job_status_update", "status": "running", "data": {}}
)
```

The consumer's `job_status_update()` handler transforms this into the client-facing `status_update` message format with sequence numbering.

## Consumer 2: TaskLogConsumer

### Purpose

Streams task log messages for a tuning job. Used by the frontend to display a live log feed during job execution.

### Channel Group

Group name: `job_{job_id}_logs`

### Connection Sequence

1. Validate JWT authentication
2. Check job access permissions
3. Join channel group `job_{job_id}_logs`
4. Accept WebSocket connection
5. Start heartbeat
6. Send existing log entries (late-join support, up to 500 entries)

### Late-Join Buffer

On connection, the consumer queries existing `TaskLog` entries linked to the job (via `task__tuning_job_link__job_id`) and sends them in chronological order:

```json
{
  "type": "log",
  "message": "Started the parameter tuning job 42",
  "timestamp": "2025-01-15T10:30:00.123456+00:00",
  "buffered": true,
  "seq": 0
}
```

- Maximum `500` entries are sent (`LATE_JOIN_BUFFER_LIMIT`)
- Entries are ordered by `ins_datetime` ascending
- The `buffered: true` flag distinguishes historical entries from live messages

### Message Format: log

Sent by the consumer to clients for each log message.

```json
{
  "type": "log",
  "message": "<log text>",
  "timestamp": "<ISO 8601>",
  "seq": 0
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"log"` |
| `message` | string | Log message content |
| `timestamp` | string | ISO 8601 timestamp (may be empty for live messages) |
| `seq` | integer | Monotonically increasing sequence number per connection |

### Internal Channel Event: task_log_message

Celery tasks push log messages via:

```python
channel_layer.group_send(
    f"job_{job_id}_logs",
    {"type": "task_log_message", "message": "Trial 3 with IALSRecommender..."}
)
```

## Sequence Numbering

Both consumers maintain a per-connection sequence counter (`_seq`). Every outbound message includes a `seq` field that starts at 0 and increments by 1 for each message sent. This allows clients to:

- Detect missed messages
- Maintain message ordering
- Distinguish the initial buffer from live updates

## Complete Message Flow Diagram

```
                                            Redis (db1)
                                         Channel Layer
Client (Browser)     Consumer              Group          Celery Worker
     |                  |                    |                  |
     |  WS Connect      |                    |                  |
     |----------------->|                    |                  |
     |                  |  group_add()       |                  |
     |                  |------------------->|                  |
     |  status_update   |                    |                  |
     |  (buffered)      |                    |                  |
     |<-----------------|                    |                  |
     |                  |                    |                  |
     |                  |                    |  group_send()    |
     |                  |                    |<-----------------|
     |                  |  job_status_update |                  |
     |                  |<-------------------|                  |
     |  status_update   |                    |                  |
     |<-----------------|                    |                  |
     |                  |                    |                  |
     |  {"type":"ping"} |                    |                  |
     |<-----------------|                    |                  |
     |  {"type":"pong"} |                    |                  |
     |----------------->|                    |                  |
     |                  |                    |                  |
     |  WS Disconnect   |                    |                  |
     |----------------->|                    |                  |
     |                  |  group_discard()   |                  |
     |                  |------------------->|                  |
```

## nginx WebSocket Configuration

WebSocket connections are proxied through nginx with the following configuration:

```nginx
location /ws/ {
    access_log /dev/stdout ws_sanitized;
    proxy_pass http://backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 300s;
}
```

**Key points**:
- The `ws_sanitized` log format excludes query strings to prevent JWT token leakage in access logs.
- `proxy_read_timeout 300s` allows long-lived connections (5 minutes before nginx drops idle connections; heartbeat at 60s keeps them alive).
- The `Upgrade` and `Connection` headers enable the HTTP-to-WebSocket protocol switch.

## Error Handling

- If `_get_existing_logs()` fails during late-join buffer delivery, the error is logged but the connection remains open. Live messages will still be delivered.
- If `group_send()` fails in Celery tasks (e.g., Redis connection error), the exception is caught and logged as a warning. The task continues execution; log entries are also persisted to the database as `TaskLog` records.
- The heartbeat loop gracefully handles `CancelledError` on disconnect.
