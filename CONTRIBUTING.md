# Contributing to Recotem

Thank you for your interest in contributing to Recotem! This guide will help you get started.

## Architecture Overview

Recotem is a recommendation system platform built with:

```
┌──────────────────────────────────────────────────────┐
│                     Clients                          │
│              (Browser / API consumers)               │
└────────────────────────┬─────────────────────────────┘
                         │ X-API-Key or JWT
                   ┌─────▼─────┐
                   │   nginx   │ :8000
                   │  (proxy)  │ SPA + reverse proxy
                   └──┬────┬───┘
                      │    │
          ┌───────────▼┐  ┌▼───────────┐  ┌───────────┐
          │  Frontend  │  │  Backend   │  │ Inference │
          │  Vue 3 SPA │  │  Django 5  │  │ FastAPI   │
          │  (static)  │  │  (daphne)  │  │ :8081     │
          └────────────┘  └──┬──┬──┬───┘  └──┬────┬───┘
                             │  │  │         │    │ read-only
                    ┌────────▼┐ │ ┌▼─────────▼┐  │
                    │ Celery  │ │ │ PostgreSQL │  │
                    │ Worker  │ │ │   :5432    │  │
                    └────┬────┘ │ └────────────┘  │
                         │     │                  │
                    ┌────▼─────▼──────────────────▼┐
                    │          Redis :6379          │
                    │ db0=broker  db1=channels      │
                    │ db2=cache   db3=model events  │
                    └──────────┬───────────────────┘
                          ┌────▼────┐
                          │ Celery  │
                          │  Beat   │
                          └─────────┘
```

### Services (Docker Compose)

| Service | Image/Build | Role |
|---------|------------|------|
| `db` | postgres:17-alpine | Primary data store |
| `redis` | redis:7-alpine | Broker (db 0), Channels (db 1), Cache (db 2), Model events (db 3) |
| `backend` | `backend/Dockerfile` | Django ASGI server (daphne), REST API + WebSocket |
| `worker` | `backend/Dockerfile` | Celery worker for async tuning/training tasks |
| `beat` | `backend/Dockerfile` | Celery Beat for scheduled retraining |
| `inference` | `inference/Dockerfile` | FastAPI inference service for real-time recommendations |
| `proxy` | `proxy.dockerfile` | nginx serving Vue SPA + reverse proxy to backend + inference |

### Data Flow

1. **User uploads training data** → REST API → stored in filesystem/S3
2. **User creates a tuning job** → REST API → Celery task queued
3. **Worker runs hyperparameter optimization** (irspack + Optuna) → logs + status updates via WebSocket
4. **Best model configuration found** → stored in DB → optional auto-train
5. **User requests recommendations** → REST API → trained model loaded from cache → JSON response

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js 22+
- Docker and Docker Compose
- Git

## Development Environment Setup

### 1. Clone the repository

```bash
git clone https://github.com/tohtsky/recotem.git
cd recotem
```

### 2. Set up environment files

```bash
make setup-env
```

This copies `envs/dev.env.example` to `envs/dev.env`. Edit `envs/dev.env` to set your local passwords and secrets. The actual env files are gitignored; only the `.example` templates are tracked.

### 3. Start infrastructure services

```bash
docker compose -f compose-dev.yaml up -d
```

This starts PostgreSQL and Redis containers for local development.

### 4. Backend setup

```bash
cd backend
uv sync
cd recotem
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run daphne recotem.asgi:application -b 0.0.0.0 -p 8000
```

### 5. Celery worker (separate terminal)

```bash
cd backend/recotem
uv run celery -A recotem worker --loglevel=INFO
```

### 6. Frontend setup (separate terminal)

```bash
cd frontend
npm install
npm run dev
```

The frontend dev server runs at http://localhost:5173 with API proxy to the backend.

## Project Structure

```
recotem/
  backend/
    recotem/              # Django project
      recotem/
        api/
          authentication.py  # API key authentication
          consumers.py       # WebSocket consumers
          models/            # Django models
          serializers/       # DRF serializers (api_key, retraining, ab_test, etc.)
          services/          # Business logic (training, model, scheduling, A/B testing)
          tasks.py           # Celery tasks (tuning, training, retraining)
          views/             # DRF viewsets
        settings.py
      manage.py
      tests/              # pytest tests
    pyproject.toml        # Dependencies + tool config (uv)
    uv.lock               # Locked dependency versions
    Dockerfile            # Multi-stage (backend + worker + beat)
  inference/              # Standalone FastAPI inference service
    inference/
      routes/             # Prediction, project-level, health endpoints
      auth.py             # API key verification (Django-compatible)
      config.py           # Pydantic Settings
      model_loader.py     # LRU model cache with hot-swap
      hot_swap.py         # Redis Pub/Sub listener
      models.py           # SQLAlchemy models (read-only)
    Dockerfile
    pyproject.toml
  frontend/
    src/
      api/                # API client (ofetch), generated types
      components/         # Reusable Vue components
        common/           # Generic components (FormField, etc.)
        layout/           # Layout components (SidebarNav, etc.)
      composables/        # Vue composables (useWebSocket, useJobStatus, etc.)
      layouts/            # Page layout wrappers
      pages/              # Route page components
      stores/             # Pinia stores (auth, project)
      types/              # TypeScript type definitions + WebSocket types
    e2e/                  # Playwright E2E tests
  compose.yaml            # Production Docker Compose (7 services)
  compose-dev.yaml        # Development Docker Compose (db + redis)
  proxy.dockerfile        # nginx + frontend SPA build
  nginx.conf              # SPA + API + WS + Admin + Inference proxy
  helm/recotem/           # Helm chart for Kubernetes deployment
  docs/
    guide/                # User-facing guides (getting started, tuning, training, etc.)
    specification/        # Developer-facing specs (architecture, data model, API, security)
    deployment/           # Deployment and operations documentation
```

## API Development Guide

### Adding a new endpoint

1. **Model** (if needed): Add to `backend/recotem/recotem/api/models/__init__.py`, create migration:
   ```bash
   cd backend/recotem && uv run python manage.py makemigrations api
   ```

2. **Serializer**: Add to `backend/recotem/recotem/api/serializers.py`

3. **ViewSet**: Create in `backend/recotem/recotem/api/views/`. Follow the owner-filtering pattern:
   ```python
   def get_queryset(self):
       return MyModel.objects.filter(
           Q(project__owner=self.request.user) | Q(project__owner__isnull=True)
       )

   def perform_create(self, serializer):
       serializer.save(owner=self.request.user)
   ```

4. **URL registration**: Add the router entry in `backend/recotem/recotem/api/views/__init__.py`

5. **OpenAPI schema**: Regenerate and commit:
   ```bash
   cd backend/recotem && uv run python manage.py spectacular --file ../../docs/openapi-schema.yml
   ```

6. **Frontend types**: Regenerate with `cd frontend && npm run generate:types`

7. **Tests**: Add to `backend/recotem/tests/`

### Service layer pattern

Business logic lives in `backend/recotem/recotem/api/services/`. ViewSets call services; services call models. This keeps views thin and business logic testable.

## Frontend Development Guide

### Component patterns

- **Pages**: Full-page components in `src/pages/`. One per route. Handle data fetching, state coordination.
- **Components**: Reusable UI in `src/components/`. Receive data via props, emit events.
- **Composables**: Shared logic in `src/composables/`. Use `useXxx()` naming convention.
- **Stores**: Global state in `src/stores/`. Use Pinia with Composition API style.

### API calls

Use the `api()` client from `@/api/client`:

```typescript
import { api } from "@/api/client";
import type { Project, PaginatedResponse } from "@/types";

const data = await api<PaginatedResponse<Project>>("/projects/");
```

### Type generation workflow

Types are defined in `frontend/src/types/index.ts`. When the backend schema changes:

```bash
# Start backend locally, then:
cd frontend
npm run generate:types    # Generates src/api/generated-types.ts
```

WebSocket message types are maintained manually in `src/types/websocket.ts` (not in OpenAPI).

### UI components

Use **PrimeVue 4** components. Import from `primevue/xxx`:

```typescript
import DataTable from "primevue/datatable";
import Button from "primevue/button";
```

Style with **Tailwind CSS 4**. Use the `@theme` tokens defined in `src/styles/main.css`.

### Internationalization (i18n)

All user-visible strings must be externalized using **vue-i18n**:

1. Add keys to both `frontend/src/i18n/locales/en.json` and `ja.json`
2. Use `$t('key')` in templates or `t('key')` from `useI18n()` in `<script setup>`
3. For non-component contexts (e.g., API client), use `i18n.global.t('key')`
4. Use reusable components (`EmptyState`, `PageHeader`, `FormField`) instead of inline patterns

## Coding Standards

### Python (Backend)

- Formatter/linter: **ruff** (configured in `pyproject.toml`)
- Line length: 88 characters
- All code must pass `ruff check` and `ruff format --check`
- Use type hints for function signatures
- Write docstrings for public APIs

### TypeScript/Vue (Frontend)

- Framework: Vue 3 with Composition API (`<script setup>`)
- UI library: PrimeVue 4
- State management: Pinia
- Follow the existing component patterns in `src/components/`

### General

- All code comments, docstrings, and documentation in English
- Commit messages in English

## Testing

### Backend tests

```bash
cd backend
uv run pytest recotem/tests/ --cov
```

### Frontend tests

```bash
cd frontend
npm run test:unit
```

### Frontend E2E tests

Create dedicated users first (recommended):

```bash
cd backend/recotem
uv run python manage.py create_test_users \
  --user e2e_user_a:e2e_password_a \
  --user e2e_user_b:e2e_password_b
```

Then run Playwright:

```bash
cd frontend
E2E_BASE_URL=http://localhost:8000 \
E2E_USER_A_USERNAME=e2e_user_a \
E2E_USER_A_PASSWORD=e2e_password_a \
E2E_USER_B_USERNAME=e2e_user_b \
E2E_USER_B_PASSWORD=e2e_password_b \
E2E_API_BASE_URL=http://localhost:8000/api/v1 \
npm run test:e2e
```

## Troubleshooting

### Database connection errors

Ensure PostgreSQL is running: `docker compose -f compose-dev.yaml ps`

Check `DATABASE_URL` environment variable (default: `postgresql://recotem_user:recotem_password@localhost:5432/recotem`).

### Redis connection errors

Ensure Redis is running: `docker compose -f compose-dev.yaml ps`

If using `REDIS_PASSWORD`, the backend auto-injects it into broker/cache URLs.

### Celery tasks not executing

1. Check the worker is running: `uv run celery -A recotem inspect active`
2. Check Redis connectivity: `redis-cli ping`
3. Verify `CELERY_BROKER_URL` matches the Redis instance

### Frontend dev proxy issues

The Vite dev server proxies `/api` and `/ws` to `http://localhost:8000`. If the backend is on a different port, update `frontend/vite.config.ts`.

### Migration errors after model changes

```bash
cd backend/recotem
uv run python manage.py makemigrations api
uv run python manage.py migrate
```

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes with clear, focused commits
3. Ensure all tests pass
4. Update documentation if needed
5. Submit a pull request using the PR template

### Branch naming

- `feature/description` for new features
- `fix/description` for bug fixes
- `docs/description` for documentation changes

### Commit messages

Use clear, concise commit messages that describe what changed and why.

## Code of Conduct

We are committed to providing a welcoming and inclusive experience for everyone. Please be respectful and constructive in all interactions.

## Code Review

All pull requests require at least one review before merging. Reviewers will check:

- Code quality and adherence to project conventions
- Test coverage for new functionality
- Documentation updates where needed
- No security vulnerabilities introduced
