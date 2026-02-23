# Inference API

The inference API is how your application gets recommendations from Recotem. After you have trained a recommendation model, the inference API is the endpoint your app calls to ask "what items should I recommend to this user?" and get an answer back in milliseconds.

## Where Inference Fits in the Workflow

The inference API is the final step in the recommendation pipeline:

1. **Upload data** -- you provide user interaction data (e.g., clicks, purchases) to Recotem.
2. **Tune and train** -- Recotem finds the best algorithm settings and trains a model.
3. **Deploy** -- you assign the trained model to a deployment slot so it can serve predictions.
4. **Get recommendations (you are here)** -- your application calls the inference API with a user ID and gets back a ranked list of recommended items.

The inference service is a standalone FastAPI application that serves real-time recommendations. It runs independently from the Django backend and connects directly to PostgreSQL and Redis. Database access is read-only. This separation means the inference service can be scaled independently to handle high prediction traffic without affecting the management interface.

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

You need to create an API key before you can call the inference API. See the [API Keys guide](api-keys.md) for how to create and manage keys.

## Endpoints

### POST /inference/predict/{model_id}

Get top-K recommendations for a single user from a specific model. This is the simplest endpoint -- use it when you know exactly which trained model you want to query.

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

Get recommendations for multiple users in a single request (max 100 users). This is more efficient than making individual calls when you need to generate recommendations for a batch of users at once, such as for email campaigns or pre-computed recommendation feeds.

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

Get recommendations using the project's deployment slots. Instead of specifying a model directly, you point to a project and Recotem automatically selects which model to use based on your deployment slot weights. This is the recommended endpoint for production use, as it enables A/B testing and seamless model updates without changing your application code. See the [A/B Testing guide](ab-testing.md) for details on setting up experiments.

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

**Recording events**: Use the `request_id` from the response to record impression and conversion events via `POST /api/v1/conversion_event/`. See the [A/B Testing guide](ab-testing.md) for details.

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

When you train a new model, you do not need to restart the inference service. Recotem automatically pushes model updates to the inference service in the background.

Here is how it works: the backend publishes a `model_trained` event to Redis Pub/Sub (channel `recotem:model_events` on db 3). Each inference service replica independently receives the event and loads the new model in a background thread.

This means:
- No restart needed when models are updated
- All replicas update independently
- Model loading happens in the background without blocking ongoing requests
- Old models remain available until they are evicted from the LRU cache

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `INFERENCE_PORT` | `8081` | Port the inference service listens on |
| `INFERENCE_MAX_LOADED_MODELS` | `10` | Maximum models in the LRU cache |
| `INFERENCE_RATE_LIMIT` | `100/minute` | Rate limit per API key |
| `DATABASE_URL` | (required) | PostgreSQL connection |
| `SECRET_KEY` | (required) | Must match the backend's secret for HMAC verification |
| `MODEL_EVENTS_REDIS_URL` | `redis://localhost:6379/3` | Redis URL for model event Pub/Sub |

## Rate Limiting

The inference service applies rate limits per API key using the `slowapi` library. The default is 100 requests per minute. Exceeding the limit returns HTTP 429.

## Scaling

The inference service is stateless — each replica loads models independently into memory. Scale horizontally by increasing replicas:

- **Docker Compose**: `docker compose up --scale inference=3`
- **Kubernetes**: Adjust `inference.replicaCount` in Helm values, or enable HPA

Each replica subscribes to Redis Pub/Sub independently, so all replicas receive model update events.
