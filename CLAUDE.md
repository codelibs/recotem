# Recotem

Recommender system builder — Docker-first web app for tuning, training, deploying, and monitoring recommendation models via UI and REST API.

## Architecture

**7 Docker services** (see `compose.yaml`):
- `db` — PostgreSQL 17
- `redis` — Redis 7 (broker db0, channels db1, cache db2, model events db3)
- `backend` — Django 5.1 + DRF + Channels, served by Daphne (ASGI)
- `worker` — Celery (same Docker image as backend)
- `beat` — Celery Beat for scheduled retraining (same Docker image as backend)
- `inference` — FastAPI service for real-time recommendations (separate image)
- `proxy` — Nginx + Vue 3 SPA (built in `proxy.dockerfile`)

**Inference-only deployment** (see `compose-inference.yaml`):
- `db` — PostgreSQL 17
- `inference` — FastAPI service
- `proxy` — Nginx (inference routes only, via `nginx-inference.conf`)

**ML stack**: irspack 0.4.0 + Optuna for hyperparameter tuning.

## Directory Layout

```
backend/
  Dockerfile              # Multi-stage: base → builder → testing → production
  pyproject.toml          # Project metadata, dependencies, Ruff config
  uv.lock                 # Locked dependency versions (committed)
  recotem/
    recotem/
      settings.py         # Django settings (django-environ)
      urls.py             # Root URL conf: /api/v1/, /admin/, /ws/
      asgi.py / celery.py
      api/
        authentication.py # API key auth (X-API-Key header)
        models/           # Django models (Project, TrainingData, TrainedModel, ApiKey, etc.)
        views/            # DRF ViewSets + mixins (OwnedResourceMixin, CreatedByResourceMixin)
        serializers/      # DRF serializers (api_key, retraining, deployment, ab_test, events)
        services/         # Business logic (model, training, tuning, pickle_signing, scheduling, ab_testing)
        tasks.py          # Celery tasks (tuning, training, scheduled retraining)
        consumers.py      # WebSocket consumers (Django Channels)
        urls.py           # API router registration
    tests/                # pytest + pytest-django
      conftest.py         # MovieLens100K fixtures
inference/
  Dockerfile              # Multi-stage FastAPI build
  pyproject.toml
  inference/
    main.py               # FastAPI app with lifespan + rate limiting
    config.py             # Pydantic Settings
    auth.py               # API key verification (Django PBKDF2 compatible)
    model_loader.py       # Thread-safe LRU model cache
    hot_swap.py           # Redis Pub/Sub listener for model updates
    models.py             # SQLAlchemy read-only models
    routes/               # predict, project (A/B routing), health
frontend/
  package.json            # Vue 3.5 + Vite 6 + PrimeVue 4 + Tailwind CSS 4 + Pinia + TanStack Query
  src/
    pages/                # Page components (Login, Dashboard, Data*, Tuning*, Model*, ApiKey*, ABTest*, etc.)
    layouts/              # MainLayout, AuthLayout, ProjectLayout
    stores/               # Pinia stores (auth, project, notification)
    composables/          # useWebSocket, useJobStatus, useAbortOnUnmount, useNotification
    api/                  # API client (ofetch) + production.ts
    types/                # TypeScript types + production.ts
    utils/                # format.ts (formatDate, formatFileSize, formatScore)
    router/index.ts       # Vue Router with auth guards
helm/recotem/             # Helm chart (ServiceAccount, PDB, NetworkPolicy, HPA)
envs/                     # Environment files (dev.env, production.env)
nginx.conf                # Proxy config: SPA + /api/ + /ws/ + /admin/ + /inference/ + /static/
docs/
  guides/                 # Feature guides (inference-api, api-keys, retraining, ab-testing)
  deployment/             # Deployment guides (docker-compose, kubernetes, aws, gcp, env vars)
```

## Development Setup

```bash
# Start infrastructure (postgres + redis)
docker compose -f compose-dev.yaml up -d

# Backend
cd backend
uv sync
cd recotem
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run daphne recotem.asgi:application -b 0.0.0.0 -p 8000

# Celery worker (separate terminal)
cd backend/recotem
uv run celery -A recotem worker --loglevel=INFO

# Celery beat (separate terminal, for scheduled retraining)
cd backend/recotem
uv run celery -A recotem beat --loglevel=INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler

# Frontend
cd frontend
npm install
npm run dev
```

Dev env variables are in `envs/dev.env`. Backend expects `DATABASE_URL`, `CELERY_BROKER_URL` pointing to local postgres:5432 and redis:6379.

## Key Commands

```bash
# Backend tests (requires MovieLens100K dataset download on first run)
cd backend && uv run pytest recotem/tests/ -v

# Frontend
cd frontend && npm run test:unit          # Vitest
cd frontend && npm run test:e2e           # Playwright
cd frontend && npm run type-check         # vue-tsc
cd frontend && npm run lint               # ESLint

# Python linting
ruff check backend/ --fix
ruff format backend/

# OpenAPI type generation
cd frontend && npm run generate:types     # Requires backend running on :8000

# Full production stack
docker compose up --build
```

## API Structure

### Management API

Base path: `/api/v1/` (backward compat at `/api/`)

| Endpoint | ViewSet |
|---|---|
| `project/` | ProjectViewSet |
| `training_data/` | TrainingDataViewset |
| `item_meta_data/` | ItemMetaDataViewset |
| `split_config/` | SplitConfigViewSet |
| `evaluation_config/` | EvaluationConfigViewSet |
| `parameter_tuning_job/` | ParameterTuningJobViewSet |
| `model_configuration/` | ModelConfigurationViewset |
| `trained_model/` | TrainedModelViewset |
| `task_log/` | TaskLogViewSet |
| `api_keys/` | ApiKeyViewSet |
| `retraining_schedule/` | RetrainingScheduleViewSet |
| `retraining_run/` | RetrainingRunViewSet |
| `deployment_slot/` | DeploymentSlotViewSet |
| `ab_test/` | ABTestViewSet |
| `conversion_event/` | ConversionEventViewSet |
| `ping/` | PingView |
| `project_summary/<id>/` | ProjectSummaryView |
| `auth/login/` | dj-rest-auth LoginView |
| `schema/` | drf-spectacular |

Auth: JWT via dj-rest-auth + simplejwt, or API key via `X-API-Key` header.

WebSocket: `/ws/job/{id}/` for job status updates.

### Inference API

Base path: `/inference/` (proxied to FastAPI service on port 8081)

| Endpoint | Description |
|---|---|
| `POST /inference/predict/{model_id}` | Single-user recommendations |
| `POST /inference/predict/{model_id}/batch` | Multi-user batch recommendations |
| `POST /inference/predict/project/{project_id}` | Project-level with A/B slot routing |
| `GET /inference/health` | Health check |
| `GET /inference/models` | List loaded models |

Auth: API key with `predict` scope via `X-API-Key` header.

## Data Flow

1. Create Project (user/item/time column definitions)
2. Upload TrainingData CSV
3. Create SplitConfig + EvaluationConfig
4. Create ParameterTuningJob → Celery task runs Optuna + irspack
5. Best ModelConfiguration saved automatically
6. Train model (auto or manual) → TrainedModel with HMAC-signed serialized file
7. Create API key with `predict` scope
8. Create DeploymentSlot(s) pointing to trained model(s)
9. Call inference API → weighted slot selection → real-time recommendations
10. Record ConversionEvents → analyze A/B test results

## Conventions

- **Python**: 3.12, uv for dependency management, Ruff for linting/formatting (line-length 88, rules: E/F/I/W/UP/B/SIM)
- **Frontend**: TypeScript strict, Vue 3 Composition API, PrimeVue components, Tailwind CSS 4
- **ViewSets**: Use `OwnedResourceMixin` / `CreatedByResourceMixin` from `views/mixins.py` for ownership filtering
- **Services**: Business logic in `api/services/`, not in views
- **Model security**: HMAC-SHA256 signing via `pickle_signing_core.py` (Django-independent) — models signed on save, verified on load
- **Uniqueness**: Project.name is per-owner, ModelConfiguration.name is per-training-data
- **Backend user**: runs as `appuser:1000` in Docker
- **Proxy**: nginx on port 8000 (non-root)
- **Python package manager**: `uv` (not pip). Use `uv sync` to install, `uv run` to execute
- **Frontend package manager**: use `npm` (not pnpm)

## Environment Variables

Core (see `envs/.env.example`):
- `DATABASE_URL` — PostgreSQL connection string
- `CELERY_BROKER_URL` — Redis URL for Celery (db 0)
- `CACHE_REDIS_URL` — Redis URL for cache (db 2)
- `MODEL_EVENTS_REDIS_URL` — Redis URL for model event Pub/Sub (db 3)
- `SECRET_KEY` — Django secret (must change for production)
- `DEBUG` — true/false
- `DEFAULT_ADMIN_PASSWORD` — Initial admin password
- `REDIS_PASSWORD` — Optional Redis auth
- `ACCESS_TOKEN_LIFETIME` — JWT access token TTL in seconds (default 300)
- `RECOTEM_STORAGE_TYPE` — Empty for local, "S3" for S3 storage
- `CELERY_TASK_TIME_LIMIT` — Task timeout in seconds (default 3600)
- `CELERY_BEAT_SCHEDULER` — `django_celery_beat.schedulers:DatabaseScheduler`
- `INFERENCE_MAX_LOADED_MODELS` — Max models in inference LRU cache (default 10)
- `INFERENCE_RATE_LIMIT` — Inference rate limit per API key (default 100/minute)
- `INFERENCE_PRELOAD_MODEL_IDS` — Comma-separated model IDs to pre-load on startup
- `LOG_LEVEL`, `DJANGO_LOG_LEVEL`, `CELERY_LOG_LEVEL` — Logging levels

## CI/CD

GitHub Actions (`.github/workflows/`):
- `pre-commit.yml` — Ruff + basic hooks
- `run-test.yml` — Playwright + pytest + coverage
- `release.yml` — Multi-arch container build/push + Trivy scan
- `codeql.yml` — CodeQL analysis
