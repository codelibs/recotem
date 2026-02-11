# Recotem

Recotem is an open-source recommendation system platform. Build, tune, train, deploy, and monitor recommendation models — all from a web UI or REST API.

Recotem is licensed under Apache 2.0.

## Features

- **Hyperparameter Tuning** — Automated search using Optuna + irspack with real-time progress via WebSocket
- **Model Training** — Train recommendation models from uploaded interaction data with HMAC-signed serialization
- **Inference API** — Low-latency FastAPI service for real-time recommendations with hot-swap model loading
- **API Key Authentication** — Project-scoped API keys with granular permissions (`read`, `write`, `predict`)
- **Scheduled Retraining** — Cron-based automatic retraining with django-celery-beat
- **A/B Testing** — Weighted traffic splitting across deployment slots with statistical analysis

## Architecture

```
                    ┌──────────────────┐
                    │  Clients         │
                    │  (Browser / API) │
                    └────────┬─────────┘
                             │ X-API-Key or JWT
                    ┌────────▼─────────┐
                    │  nginx (proxy)   │ :8000
                    └──┬─────┬─────┬───┘
                       │     │     │
         /api/ /ws/    │     │     │  /inference/
         /admin/       │     │     │
            ┌──────────▼┐  ┌▼─────▼──────────┐
            │  Backend  │  │  Inference       │
            │  Django 5 │  │  FastAPI         │
            │  (daphne) │  │  :8081           │
            └──┬────┬───┘  └──┬────┬─────────┘
               │    │         │    │ read-only
          ┌────▼┐  ┌▼────┐   │  ┌─▼──────────┐
          │Redis│  │Celery│   │  │ PostgreSQL  │
          │     │  │Worker│   │  │ :5432       │
          │db0-3│  └──────┘   │  └─────────────┘
          └──┬──┘             │
             │    ┌───────────┘
          ┌──▼────▼──┐
          │  Celery  │
          │  Beat    │
          └──────────┘

Redis databases:
  db0 = Celery broker    db2 = Django cache
  db1 = Channels (WS)    db3 = Model event Pub/Sub
```

**7 services**: PostgreSQL, Redis, Backend (Django), Worker (Celery), Beat (Celery Beat), Inference (FastAPI), Proxy (nginx + SPA)

## Quick Start

### Docker Compose

```bash
docker compose up
```

The app is available at `http://localhost:8000`. Default admin credentials are configured via environment variables.

> **Production:** Copy `envs/.env.example` to `envs/production.env` and change all `CHANGE_ME_*` values before deploying.

### Pre-built Images

1. Visit the [latest release](https://github.com/codelibs/recotem/releases/latest)
2. Download "Docker resources to try out" from Assets
3. Unzip and run `docker compose up`

## Usage Workflow

1. **Create a Project** — Define user/item/time column names
2. **Upload Training Data** — CSV file with interaction records
3. **Tune Hyperparameters** — Run Optuna-based search across irspack algorithms
4. **Train a Model** — Use the best configuration (auto or manual)
5. **Create an API Key** — Generate a `rctm_`-prefixed key with `predict` scope
6. **Deploy to Inference** — Create a deployment slot pointing to a trained model
7. **Get Recommendations** — Call the inference API with your API key

```bash
# Example: get recommendations
curl -X POST http://localhost:8000/inference/predict/project/1 \
  -H "X-API-Key: rctm_your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "42", "cutoff": 10}'
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full developer guide.

### Prerequisites

- Python 3.12+, [uv](https://docs.astral.sh/uv/)
- Node.js 22+
- Docker and Docker Compose

### Quick Setup

```bash
# Infrastructure (PostgreSQL + Redis)
docker compose -f compose-dev.yaml up -d

# Backend
cd backend && uv sync
cd recotem
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run daphne recotem.asgi:application -b 0.0.0.0 -p 8000

# Celery worker (separate terminal)
cd backend/recotem
uv run celery -A recotem worker --loglevel=INFO

# Celery beat (separate terminal)
cd backend/recotem
uv run celery -A recotem beat --loglevel=INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

### Testing

```bash
# Backend
cd backend && uv run pytest recotem/tests/ -v

# Frontend
cd frontend && npm run test:unit      # Vitest
cd frontend && npm run test:e2e       # Playwright
cd frontend && npm run type-check     # vue-tsc
```

## Documentation

| Topic | Link |
|-------|------|
| Inference API | [docs/guides/inference-api.md](docs/guides/inference-api.md) |
| API Key Authentication | [docs/guides/api-keys.md](docs/guides/api-keys.md) |
| Scheduled Retraining | [docs/guides/retraining.md](docs/guides/retraining.md) |
| A/B Testing | [docs/guides/ab-testing.md](docs/guides/ab-testing.md) |
| Standalone Inference | [docs/guides/standalone-inference.md](docs/guides/standalone-inference.md) |
| Docker Compose Deployment | [docs/deployment/docker-compose.md](docs/deployment/docker-compose.md) |
| Kubernetes Deployment | [docs/deployment/kubernetes.md](docs/deployment/kubernetes.md) |
| AWS Deployment | [docs/deployment/aws.md](docs/deployment/aws.md) |
| GCP Deployment | [docs/deployment/gcp.md](docs/deployment/gcp.md) |
| Environment Variables | [docs/deployment/environment-variables.md](docs/deployment/environment-variables.md) |
| Separate Frontend | [docs/deployment/separate-frontend.md](docs/deployment/separate-frontend.md) |
| Contributing | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Links

- Website: [recotem.org](https://recotem.org)
- CLI tool: [recotem-cli](https://github.com/codelibs/recotem-cli)
- Batch on ECS: [recotem-batch-example](https://github.com/codelibs/recotem-batch-example)
- Issues / Questions: [discuss.codelibs.org](https://discuss.codelibs.org/c/recotemen/11)
