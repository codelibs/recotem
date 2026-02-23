# Inference Service Specification

## Overview

The inference service is a standalone FastAPI application that serves real-time recommendation predictions. It operates independently from the Django backend, connecting to the same PostgreSQL database and Redis instance (Pub/Sub on db3). Database access is read-only. This separation enables independent scaling and deployment of the serving layer.

## Architecture

```
+-------------------------------------------------------------+
|                     Inference Service                        |
|                     (FastAPI, port 8081)                      |
|                                                              |
|  +-------------+  +-------------+  +----------------------+ |
|  | Routes      |  | Auth        |  | Rate Limiter         | |
|  | - predict   |  | - API key   |  | - slowapi            | |
|  | - project   |  | - scope     |  | - per key/IP         | |
|  | - health    |  |   check     |  |                      | |
|  +------+------+  +------+------+  +----------------------+ |
|         |                |                                    |
|  +------v------------------------------+                     |
|  |       Model Loader                  |                     |
|  |  +------------------------------+   |                     |
|  |  |  LRU Cache (OrderedDict)     |   |                     |
|  |  |  Thread-safe (Lock)          |   |                     |
|  |  |  Max: INFERENCE_MAX_         |   |                     |
|  |  |       LOADED_MODELS          |   |                     |
|  |  +------------------------------+   |                     |
|  +------+------------------------------+                     |
|         |                                                    |
|  +------v----------+  +-----------------------+              |
|  | HMAC Verifier   |  | Hot-Swap Listener     |              |
|  | (signing.py)    |  | (Redis Pub/Sub)       |              |
|  |                 |  | Channel:              |              |
|  | SECRET_KEY      |  |  recotem:model_events |              |
|  +-----------------+  +-----------+-----------+              |
|                                   |                          |
+-----------------------------------+--------------------------+
                                    |
              +---------------------+---------------+
              |                     |               |
       +------v------+     +-------v------+ +------v------+
       | /data (ro)  |     | Redis db3    | | PostgreSQL  |
       | Model files |     | Pub/Sub      | |             |
       +-------------+     +--------------+ +-------------+
```

## Configuration

All settings are managed via Pydantic `BaseSettings` (loaded from environment variables):

| Setting | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://recotem_user:recotem_pass@localhost:5432/recotem` | PostgreSQL connection (read-only) |
| `MODEL_EVENTS_REDIS_URL` | `redis://localhost:6379/3` | Redis Pub/Sub URL (db3) |
| `SECRET_KEY` | `VeryBadSecret@ChangeThis` | Must match Django's SECRET_KEY for HMAC verification |
| `INFERENCE_PORT` | `8081` | Service listen port |
| `INFERENCE_MAX_LOADED_MODELS` | `10` | Maximum models in LRU cache |
| `INFERENCE_RATE_LIMIT` | `100/minute` | Rate limit per API key |
| `INFERENCE_PRELOAD_MODEL_IDS` | `""` | Comma-separated model IDs to load at startup |
| `MEDIA_ROOT` | `/data` | Root path for model file storage |
| `RECOTEM_STORAGE_TYPE` | `""` | Storage type (empty for local filesystem) |
| `PICKLE_ALLOW_LEGACY_UNSIGNED` | `True` | Accept unsigned legacy model files |

## Database Access

The inference service uses SQLAlchemy to access Django's PostgreSQL database in read-only mode. SQLAlchemy models mirror the Django schema:

| SQLAlchemy Model | Django Table | Purpose |
|---|---|---|
| `TrainedModel` | `api_trainedmodel` | Model file paths and metadata |
| `Project` | `api_project` | Project definitions |
| `ApiKey` | `api_apikey` | API key verification |
| `ModelConfiguration` | `api_modelconfiguration` | Configuration metadata |
| `DeploymentSlot` | `api_deploymentslot` | Slot routing for A/B tests |
| `TrainingData` | `api_trainingdata` | Training data project linkage |

Sessions are created per-request via the `get_db` FastAPI dependency and closed after request completion.

## LRU Model Cache

### Design

The `ModelCache` class implements a thread-safe LRU (Least Recently Used) cache for loaded recommendation models:

```python
class ModelCache:
    _cache: OrderedDict[int, IDMappedRecommender]
    _lock: threading.Lock
    _max_size: int
```

### Operations

| Operation | Description | Thread Safety |
|---|---|---|
| `get(model_id)` | Retrieve model, move to end (most recent) | Lock-protected |
| `put(model_id, model)` | Insert/update model; evict LRU if at capacity | Lock-protected |
| `remove(model_id)` | Remove model from cache | Lock-protected |
| `loaded_models()` | List all cached model IDs | Lock-protected |
| `size()` | Return number of cached models | Lock-protected |

### Eviction Policy

When inserting a new model into a full cache, the least recently used model (front of `OrderedDict`) is evicted:

```
Cache state (max_size=3): [A, B, C]
                           ^        ^
                           LRU      MRU

get(B)  --> Cache: [A, C, B]   (B moved to end)
put(D)  --> Cache: [C, B, D]   (A evicted)
```

### Model Loading Flow

```
get_or_load_model(model_id, file_path)
  |
  +-- Check cache: model_cache.get(model_id)
  |   +-- Cache hit: return cached model
  |
  +-- Load from disk: load_model_from_file(file_path)
  |   +-- 1. Resolve path: MEDIA_ROOT / file_path
  |   +-- 2. Read raw bytes
  |   +-- 3. Verify HMAC: verify_and_extract(SECRET_KEY, raw_data)
  |   +-- 4. Deserialize: load model from verified payload
  |   +-- 5. Extract: data["id_mapped_recommender"]
  |
  +-- Store in cache: model_cache.put(model_id, model)
  +-- Return model
```

### Custom Deserializer

A custom deserializer handles models serialized with different module paths. It redirects `IDMappedRecommender` class resolution to the local inference module's `id_mapper_compat` module, ensuring compatibility regardless of the original module path.

## Hot-Swap via Redis Pub/Sub

### Event Flow

```
Celery Worker                 Redis db3               Inference Service
     |                           |                          |
     |  train_and_save_model()   |                          |
     |  ---------------------->  |                          |
     |                           |                          |
     |  PUBLISH                  |                          |
     |  recotem:model_events     |                          |
     |  {"event":"model_trained",|                          |
     |   "model_id": 42,         |                          |
     |   "project_id": 1}        |                          |
     |  --------------------->   |                          |
     |                           |  SUBSCRIBE               |
     |                           |  recotem:model_events     |
     |                           |  <-----------------------|
     |                           |                          |
     |                           |  MESSAGE                 |
     |                           |  ----------------------> |
     |                           |                          |
     |                           |     _handle_model_event()|
     |                           |     +--------------------+
     |                           |     | 1. Parse JSON      |
     |                           |     | 2. Query DB        |
     |                           |     | 3. Load model      |
     |                           |     | 4. Update cache    |
     |                           |     +--------------------+
```

### Event Format

Published by `training_service._publish_model_event()`:

```json
{
  "event": "model_trained",
  "model_id": 42,
  "project_id": 1
}
```

### Listener Thread

The Pub/Sub listener runs as a daemon thread started during FastAPI lifespan:

```python
def start_listener() -> threading.Thread:
    thread = threading.Thread(target=_listen, daemon=True, name="model-event-listener")
    thread.start()
    return thread
```

- **Auto-reconnect**: On `redis.ConnectionError`, waits 5 seconds and reconnects
- **Error isolation**: Unexpected exceptions are logged and the listener continues
- **Channel**: `recotem:model_events`

### Event Handling

When a `model_trained` event is received:
1. Parse JSON payload
2. Query `TrainedModel` from the database via SQLAlchemy
3. If the model exists and has a file, call `get_or_load_model()`
4. The model is loaded into the LRU cache, replacing any stale version
5. Log success or failure

## Slot Routing (A/B Testing)

The project-level prediction endpoint implements weighted random routing across deployment slots:

```python
def _select_slot_by_weight(slots: list[DeploymentSlot]) -> DeploymentSlot:
    weights = [s.weight for s in slots]
    return random.choices(slots, weights=weights, k=1)[0]
```

### Routing Flow

```
POST /inference/predict/project/{project_id}
  |
  +-- 1. Verify API key has predict scope and project access
  |
  +-- 2. Query active deployment slots for project
  |      SELECT * FROM api_deploymentslot
  |      WHERE project_id = ? AND is_active = true
  |
  +-- 3. Weighted random selection
  |      Example: Slot A (weight=70), Slot B (weight=30)
  |      -> 70% of requests route to Slot A
  |
  +-- 4. Load model from selected slot
  |      get_or_load_model(slot.trained_model_id, model.file)
  |
  +-- 5. Generate recommendations
  |
  +-- 6. Return response with slot_id, slot_name, and request_id
         (enables client-side event attribution)
```

The response includes `slot_id`, `slot_name`, and `request_id` so clients can record which slot served each recommendation for A/B test analysis via `POST /api/v1/conversion_event/`.

## Rate Limiting

### Implementation

Rate limiting uses `slowapi` (a Starlette-compatible wrapper around `limits`):

```python
limiter = Limiter(key_func=get_api_key_or_ip)
```

### Rate Limit Key Resolution

```python
def get_api_key_or_ip(request: Request) -> str:
    api_key_header = request.headers.get("x-api-key", "")
    if api_key_header.startswith("rctm_") and len(api_key_header) > 13:
        return api_key_header[5:13]  # Use key prefix as rate limit key
    return get_remote_address(request)
```

- **API key requests**: Rate limited per API key prefix (first 8 chars of random part)
- **Unauthenticated requests**: Rate limited per IP address
- **Default limit**: `100/minute` (configurable via `INFERENCE_RATE_LIMIT`)

### Rate limit exceeded response

```
HTTP 429 Too Many Requests
{"error": "Rate limit exceeded: 100 per 1 minute"}
```

## Pre-Loading Models

Models can be pre-loaded into the cache at service startup via `INFERENCE_PRELOAD_MODEL_IDS`:

```bash
INFERENCE_PRELOAD_MODEL_IDS=1,5,12
```

During the FastAPI lifespan startup:
1. Parse comma-separated model IDs
2. For each ID, query the database for the model record
3. If found, call `get_or_load_model()` to load into cache
4. Log success or failure for each model

This eliminates cold-start latency for frequently-used models.

## API Key Authentication

The inference service implements its own API key verification (independent of Django):

```
Request Header: X-API-Key: rctm_aBcDeFgH...
  |
  +-- 1. Check prefix: "rctm_"
  +-- 2. Extract first 8 chars of random part as prefix
  +-- 3. Query: SELECT * FROM api_apikey WHERE key_prefix = ? AND is_active = true
  +-- 4. Check expiration
  +-- 5. Verify hash: django_pbkdf2_sha256.verify(full_key, hashed_key)
  +-- 6. Check scope: "predict" in api_key.scopes
```

Uses `passlib.hash.django_pbkdf2_sha256` for Django-compatible PBKDF2-SHA256 hash verification.

## Health Check

The `/health` endpoint returns service status and cache metrics:

```json
{
  "status": "healthy",
  "loaded_models": 3
}
```

The `/models` endpoint lists all currently cached model IDs:

```json
{
  "models": [1, 5, 12],
  "count": 3
}
```

These endpoints do not require authentication and are used by Docker health checks and monitoring systems.

## Scaling Strategy

### Horizontal Scaling

The inference service is stateless (model cache is local to each instance). Multiple instances can run behind a load balancer:

```
                 +-- Inference Instance 1 (LRU cache)
Load Balancer ---+-- Inference Instance 2 (LRU cache)
                 +-- Inference Instance 3 (LRU cache)
```

- Each instance maintains its own LRU cache
- All instances subscribe to the same Redis Pub/Sub channel
- Model loading is idempotent (safe to load concurrently)
- No database write contention (inference is read-only)

### Memory Considerations

- **Per-model memory**: Depends on the recommendation algorithm and data size
- **Cache limit**: Controlled by `INFERENCE_MAX_LOADED_MODELS` (default 10)
- **Docker memory**: 512 MB reserved, 4 GB limit
- **Eviction**: LRU eviction ensures memory stays bounded

### Connection Pooling

- SQLAlchemy engine uses default connection pooling for database access
- Redis Pub/Sub maintains a single persistent connection per instance
- nginx upstream uses `keepalive 8` connections to the inference service

## Docker Configuration

```yaml
inference:
  depends_on:
    db: { condition: service_healthy }
    redis: { condition: service_healthy }
  volumes:
    - data-location:/data:ro      # Read-only model files
  healthcheck:
    test: ["CMD-SHELL", "curl -f http://localhost:8081/health || exit 1"]
    interval: 10s
    start_period: 20s
  deploy:
    resources:
      limits: { memory: 4G }
      reservations: { memory: 512M }
```

The `/data` volume is mounted read-only (`ro`) since the inference service only reads model files; writing is done by the Celery worker.
