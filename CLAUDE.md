# Recotem

Recommender system builder — Docker-first web app for tuning, training, and previewing recommendation models via UI.

## Architecture

**5 Docker services** (see `compose.yaml`):
- `db` — PostgreSQL 17
- `redis` — Redis 7 (broker db0, channels db1, cache db2)
- `backend` — Django 5.1 + DRF + Channels, served by Daphne (ASGI)
- `worker` — Celery (same Docker image as backend)
- `proxy` — Nginx + Vue 3 SPA (built in `proxy.dockerfile`)

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
        models/           # Django models (Project, TrainingData, TrainedModel, etc.)
        views/            # DRF ViewSets + mixins (OwnedResourceMixin, CreatedByResourceMixin)
        serializers/      # DRF serializers
        services/         # Business logic (model_service, training_service, tuning_service, pickle_signing)
        tasks.py          # Celery tasks (start_tuning_job, task_train_recommender, run_search)
        consumers.py      # WebSocket consumers (Django Channels)
        urls.py           # API router registration
    tests/                # pytest + pytest-django
      conftest.py         # MovieLens100K fixtures
frontend/
  package.json            # Vue 3.5 + Vite 6 + PrimeVue 4 + Tailwind CSS 4 + Pinia + TanStack Query
  src/
    pages/                # Page components (Login, Dashboard, Data*, Tuning*, Model*)
    layouts/              # MainLayout, AuthLayout, ProjectLayout
    stores/               # Pinia stores (auth, project, notification)
    composables/          # useWebSocket, useJobStatus, useAbortOnUnmount, useNotification
    api/                  # API client (ofetch)
    types/                # TypeScript types
    utils/                # format.ts (formatDate, formatFileSize, formatScore)
    router/index.ts       # Vue Router with auth guards
helm/recotem/             # Helm chart (ServiceAccount, PDB, NetworkPolicy)
envs/                     # Environment files (dev.env, production.env)
nginx.conf                # Proxy config: SPA + /api/ + /ws/ + /admin/ + /static/
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
| `ping/` | PingView |
| `project_summary/<id>/` | ProjectSummaryView |
| `auth/login/` | dj-rest-auth LoginView |
| `schema/` | drf-spectacular |

Auth: JWT via dj-rest-auth + simplejwt. Tokens stored in localStorage.

WebSocket: `/ws/job/{id}/` for job status updates.

## Data Flow

1. Create Project (user/item/time column definitions)
2. Upload TrainingData CSV
3. Create SplitConfig + EvaluationConfig
4. Create ParameterTuningJob → Celery task runs Optuna + irspack
5. Best ModelConfiguration saved automatically
6. Train model (auto or manual) → TrainedModel with HMAC-signed serialized file
7. Preview recommendations from trained model

## Conventions

- **Python**: 3.12, uv for dependency management, Ruff for linting/formatting (line-length 88, rules: E/F/I/W/UP/B/SIM)
- **Frontend**: TypeScript strict, Vue 3 Composition API, PrimeVue components, Tailwind CSS 4
- **ViewSets**: Use `OwnedResourceMixin` / `CreatedByResourceMixin` from `views/mixins.py` for ownership filtering
- **Services**: Business logic in `api/services/`, not in views
- **Model security**: HMAC-SHA256 signing via `pickle_signing.py` — models signed on save, verified on load
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
- `SECRET_KEY` — Django secret (must change for production)
- `DEBUG` — true/false
- `DEFAULT_ADMIN_PASSWORD` — Initial admin password
- `REDIS_PASSWORD` — Optional Redis auth
- `ACCESS_TOKEN_LIFETIME` — JWT access token TTL in seconds (default 300)
- `RECOTEM_STORAGE_TYPE` — Empty for local, "S3" for S3 storage
- `CELERY_TASK_TIME_LIMIT` — Task timeout in seconds (default 3600)
- `LOG_LEVEL`, `DJANGO_LOG_LEVEL`, `CELERY_LOG_LEVEL` — Logging levels

## CI/CD

GitHub Actions (`.github/workflows/`):
- `pre-commit.yml` — Ruff + basic hooks
- `run-test.yml` — Playwright + pytest + coverage
- `release.yml` — Multi-arch container build/push + Trivy scan
- `codeql.yml` — CodeQL analysis
