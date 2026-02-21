# Standalone Inference Deployment

Deploy only the inference server for production serving after training models with the full stack.

## When to Use This Pattern

- Training is a periodic batch job (e.g., nightly cron) on a separate machine
- The inference server runs on lighter compute, closer to the application
- You want minimal operational overhead (no Django backend, Celery, or Redis needed)

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- A trained model and API key created using the full Recotem stack
- Access to the same PostgreSQL database and model file storage

## Step 1: Train Models (Full Stack)

Start the full stack and train your models:

```bash
# Start all services
docker compose up -d

# Upload data, tune hyperparameters, and train models via the UI
# or use the REST API (see docs/guide/inference.md)
```

## Step 2: Create an API Key

Create an API key for the inference service using the CLI:

```bash
docker compose run --rm backend python manage.py create_api_key \
  --project-id 1 \
  --name "production-inference" \
  --scopes predict
```

Save the printed key (e.g., `rctm_xxxxxxxx...`) — it is only shown once.

You can also create the key with an expiry:

```bash
docker compose run --rm backend python manage.py create_api_key \
  --project-id 1 \
  --name "production-inference" \
  --scopes predict \
  --expires-in-days 90
```

## Step 3: Stop the Full Stack

```bash
docker compose down
```

The database volume (`db-data`) and model files (`data-location`) persist.

## Step 4: Deploy Inference Only

```bash
docker compose -f compose-inference.yaml up -d
```

This starts only 3 services:
- **db** — PostgreSQL (reads existing data)
- **inference** — FastAPI prediction service
- **proxy** — nginx routing `/inference/` requests

## Step 5: Test

```bash
# Health check
curl http://localhost:8000/health

# Get recommendations
curl -X POST http://localhost:8000/inference/predict/1 \
  -H "X-API-Key: rctm_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "42", "cutoff": 10}'

# Project-level with A/B slot routing
curl -X POST http://localhost:8000/inference/predict/project/1 \
  -H "X-API-Key: rctm_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "42", "cutoff": 10}'

# List loaded models
curl http://localhost:8000/inference/models
```

## Pre-loading Models

By default, models are loaded on first request. To eliminate cold-start latency, pre-load models on startup:

```env
# In envs/production.env
INFERENCE_PRELOAD_MODEL_IDS=1,2,3
```

This loads the specified models into the LRU cache during startup, so the first request is fast.

## Environment Variables

The inference-only deployment uses these variables from `envs/production.env`:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes | Must match the backend's secret for HMAC model verification |
| `INFERENCE_MAX_LOADED_MODELS` | No | Max models in LRU cache (default: 10) |
| `INFERENCE_RATE_LIMIT` | No | Rate limit per API key (default: 100/minute) |
| `INFERENCE_PRELOAD_MODEL_IDS` | No | Comma-separated model IDs to pre-load |
| `INFERENCE_AUTO_RECORD_IMPRESSIONS` | No | Auto-record impressions on project predictions (default: true) |
| `MODEL_EVENTS_REDIS_URL` | No | Redis Pub/Sub for hot-swap (ignored if Redis unavailable) |

PostgreSQL variables (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `PGDATA`) are also required for the `db` service.

## Kubernetes Deployment

For Kubernetes, use the Helm chart with inference-only values:

```yaml
# values-inference-only.yaml
backend:
  enabled: false
worker:
  enabled: false
beat:
  enabled: false
redis:
  enabled: false

inference:
  enabled: true
  replicaCount: 2
  env:
    INFERENCE_PRELOAD_MODEL_IDS: "1,2,3"

# Use an external database
postgresql:
  external:
    host: your-rds-endpoint.amazonaws.com
    port: 5432
    database: recotem
```

## Architecture

```
[API Clients] → :8000 → [nginx/proxy]
                          ├── /inference/  → [inference (FastAPI)]
                          ├── /health      → [inference (FastAPI)]
                          └── /            → 404 (inference-only mode)
                                               ↑
                          [inference] ← read-only → [PostgreSQL]
```

No Redis, Celery, Django backend, or frontend SPA is deployed.
