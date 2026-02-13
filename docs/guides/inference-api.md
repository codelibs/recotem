# Inference API

The inference service is a standalone FastAPI application that serves real-time recommendations. It runs independently from the Django backend and connects directly to PostgreSQL (read-only) and Redis.

## Base URL

All inference endpoints are accessed through the nginx proxy at `/inference/`.

```
http://localhost:8000/inference/
```

## Authentication

All prediction endpoints require an API key with `predict` scope. Pass it via the `X-API-Key` header:

```bash
curl -H "X-API-Key: rctm_your_key_here" ...
```

See [API Keys](api-keys.md) for how to create and manage keys.

## Endpoints

### POST /inference/predict/{model_id}

Get top-K recommendations for a single user from a specific model.

**Request:**

```json
{
  "user_id": "42",
  "cutoff": 10
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `user_id` | string | (required) | User identifier as it appears in the training data |
| `cutoff` | integer | 10 | Number of items to return (1-1000) |

**Response:**

```json
{
  "items": [
    {"item_id": "101", "score": 0.95},
    {"item_id": "203", "score": 0.87},
    {"item_id": "45",  "score": 0.82}
  ],
  "model_id": 1,
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**Errors:**

| Status | Cause |
|--------|-------|
| 401 | Missing or invalid API key |
| 404 | Model not found, or user not in model |
| 500 | Model file could not be loaded |

### POST /inference/predict/{model_id}/batch

Get recommendations for multiple users in a single request (max 100 users).

**Request:**

```json
{
  "user_ids": ["42", "99", "7"],
  "cutoff": 10
}
```

**Response:**

```json
{
  "results": [
    {
      "items": [{"item_id": "101", "score": 0.95}],
      "model_id": 1,
      "request_id": "..."
    },
    {
      "items": [{"item_id": "55", "score": 0.88}],
      "model_id": 1,
      "request_id": "..."
    },
    {
      "items": [],
      "model_id": 1,
      "request_id": "..."
    }
  ]
}
```

Users not found in the model return an empty `items` list (no error).

### POST /inference/predict/project/{project_id}

Get recommendations using the project's deployment slots. The inference service selects a model based on deployment slot weights, enabling A/B testing.

**Request:**

```json
{
  "user_id": "42",
  "cutoff": 10
}
```

**Response:**

```json
{
  "items": [
    {"item_id": "101", "score": 0.95}
  ],
  "model_id": 3,
  "slot_id": 2,
  "slot_name": "Variant A",
  "request_id": "a1b2c3d4-..."
}
```

The response includes `slot_id` and `slot_name` so you can track which model variant served the request. Use `request_id` when recording conversion events for A/B test analysis.

**Errors:**

| Status | Cause |
|--------|-------|
| 403 | API key not authorized for this project |
| 404 | No active deployment slots, or user not in model |

### GET /inference/health

Health check endpoint (no authentication required).

```json
{
  "status": "healthy",
  "loaded_models": 3
}
```

### GET /inference/models

List currently loaded models (no authentication required).

```json
{
  "models": [1, 3, 7],
  "count": 3
}
```

## Model Hot-Swap

When a new model is trained via the backend, the training service publishes a `model_trained` event to Redis Pub/Sub (channel `recotem:model_events` on db 3). Each inference service replica independently receives the event and loads the new model in a background thread.

This means:
- No restart needed when models are updated
- All replicas update independently
- Model loading happens in the background without blocking requests
- Old models remain available until replaced in the LRU cache

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `INFERENCE_PORT` | `8081` | Port the inference service listens on |
| `INFERENCE_MAX_LOADED_MODELS` | `10` | Maximum models in the LRU cache |
| `INFERENCE_RATE_LIMIT` | `100/minute` | Rate limit per API key |
| `DATABASE_URL` | (required) | PostgreSQL connection (read-only access) |
| `SECRET_KEY` | (required) | Must match the backend's secret for HMAC verification |
| `MODEL_EVENTS_REDIS_URL` | `redis://localhost:6379/3` | Redis URL for model event Pub/Sub |

## Rate Limiting

The inference service applies rate limits per API key using the `slowapi` library. The default is 100 requests per minute. Exceeding the limit returns HTTP 429.

## Scaling

The inference service is stateless — each replica loads models independently into memory. Scale horizontally by increasing replicas:

- **Docker Compose**: `docker compose up --scale inference=3`
- **Kubernetes**: Adjust `inference.replicaCount` in Helm values, or enable HPA

Each replica subscribes to Redis Pub/Sub independently, so all replicas receive model update events.
