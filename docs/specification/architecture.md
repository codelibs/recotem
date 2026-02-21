# Architecture Specification

## Overview

Recotem is a Docker-first web application for building, tuning, training, deploying, and monitoring recommender models through a UI and REST API. The system follows a service-oriented architecture with 7 Docker services communicating over a single bridge network.

## System Architecture Diagram

```
                        ┌────────────────────┐
                        │  Clients           │
                        │  (Browser / API)   │
                        └─────────┬──────────┘
                                  │ X-API-Key or JWT
                         ┌────────▼─────────┐
                         │  nginx (proxy)   │ :8000
                         │  + Vue 3 SPA     │
                         └──┬────┬────┬─────┘
                            │    │    │
              /api/ /ws/    │    │    │  /inference/
              /admin/       │    │    │
                 ┌──────────▼┐  ┌▼────▼──────────┐
                 │  Backend  │  │  Inference      │
                 │  Django 5 │  │  FastAPI        │
                 │ (daphne)  │  │  :8081          │
                 │  :8080    │  │                 │
                 └──┬──┬──┬──┘  └──┬────┬────────┘
                    │  │  │        │    │ read-only
               ┌────▼┐│  │        │  ┌─▼──────────┐
               │Redis││  │        │  │ PostgreSQL  │
               │     ││  │        │  │ :5432       │
               │db0-3│├──┘        │  └──────▲──────┘
               └──┬──┘│           │         │
                  │  ┌─▼───────┐  │         │
                  │  │ Celery  │  │         │
                  │  │ Worker  ├──┼─────────┘
                  │  └─────────┘  │
                  │               │
               ┌──▼───────────────┘
               │  Celery Beat
               │  (scheduler)
               └──────────────────

    Redis databases:
      db0 = Celery broker        db2 = Django cache
      db1 = Channels (WS)        db3 = Model event Pub/Sub
```

## Service Inventory

| Service | Image / Technology | Internal Port | Purpose |
|---|---|---|---|
| `db` | PostgreSQL 17.2 (Alpine) | 5432 | Persistent data store; Optuna study storage |
| `redis` | Redis 7.4 (Alpine) | 6379 | Broker, channel layer, cache, model events |
| `backend` | Django 5.1 + Daphne (ASGI) | 8080 | REST API, WebSocket, Django Admin |
| `worker` | Celery (same image as backend) | -- | Background tasks (tuning, training) |
| `beat` | Celery Beat (same image as backend) | -- | Scheduled retraining cron |
| `inference` | FastAPI + Uvicorn | 8081 | Real-time recommendation serving |
| `proxy` | nginx + Vue 3 SPA | 8000 (exposed) | Reverse proxy, static SPA hosting |

## Service Details

### 1. PostgreSQL (`db`)

- **Image**: `postgres:17.2-alpine`
- **Volume**: `db-data` mounted at `/var/lib/postgresql/data/pgdata`
- **Health check**: `pg_isready -U recotem_user -d recotem` every 5 seconds
- **Responsibilities**:
  - Application data (projects, models, training data metadata, API keys)
  - Optuna study storage (hyperparameter tuning trials)
  - Celery task results (`django-celery-results`)
  - Celery Beat schedule storage (`django-celery-beat`)

### 2. Redis (`redis`)

- **Image**: `redis:7.4-alpine`
- **Memory limit**: 256 MB with `allkeys-lru` eviction policy
- **Optional auth**: `REDIS_PASSWORD` environment variable
- **Health check**: `redis-cli ping` every 5 seconds
- **Database allocation**:

  | DB | Purpose | Consumer |
  |---|---|---|
  | db0 | Celery broker | Worker, Beat, Backend |
  | db1 | Django Channels layer | Backend (WebSocket) |
  | db2 | Django cache | Backend |
  | db3 | Model event Pub/Sub | Backend (publisher), Inference (subscriber) |

### 3. Backend (`backend`)

- **Framework**: Django 5.1 + Django REST Framework + Django Channels
- **ASGI server**: Daphne (serves both HTTP and WebSocket)
- **Internal port**: 8080
- **User**: `appuser:1000` (non-root)
- **Volumes**:
  - `data-location:/data` -- trained model files and uploaded datasets
  - `static-files:/app/dist/static` -- Django Admin static assets
- **Health check**: `curl -f http://localhost:8080/api/ping/` every 10 seconds
- **Memory**: 512 MB reserved, 2 GB limit
- **Key dependencies**:
  - `dj-rest-auth` + `djangorestframework-simplejwt` for JWT authentication
  - `django-channels` + `channels-redis` for WebSocket
  - `django-celery-results` for task result persistence
  - `drf-spectacular` for OpenAPI schema generation
  - `django-environ` for settings management
  - `irspack 0.4.0` + `optuna` for ML operations

### 4. Celery Worker (`worker`)

- **Image**: Same Docker image as backend
- **Command**: `celery -A recotem worker --loglevel=INFO`
- **Health check**: `celery inspect ping` every 60 seconds
- **Memory**: 1 GB reserved, 4 GB limit (model training is memory-intensive)
- **Volumes**: `data-location:/data` -- reads training data, writes model files
- **Responsibilities**:
  - Parameter tuning (parallel Optuna trials via `group`)
  - Model training (irspack recommender training)
  - Scheduled retraining execution
  - WebSocket status/log push via Django Channels layer
- **Auto-retry**: `ConnectionError` and `OSError` with exponential backoff, max 3 retries
- **Time limits**: Controlled via `CELERY_TASK_TIME_LIMIT` (default 3600s hard) and `CELERY_TASK_SOFT_TIME_LIMIT` (default 3480s soft)

### 5. Celery Beat (`beat`)

- **Image**: Same Docker image as backend
- **Command**: `celery -A recotem beat --loglevel=INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler`
- **Memory**: 128 MB reserved, 512 MB limit
- **Depends on**: `backend` (healthy) and `redis` (healthy)
- **Responsibilities**:
  - Reads cron schedules from `RetrainingSchedule` model via database scheduler
  - Dispatches `task_scheduled_retrain` tasks to the worker

### 6. Inference Service (`inference`)

- **Framework**: FastAPI
- **Internal port**: 8081
- **Health check**: `curl -f http://localhost:8081/health` every 10 seconds
- **Memory**: 512 MB reserved, 4 GB limit
- **Volume**: `data-location:/data:ro` (read-only access to model files)
- **Design**:
  - Separate Python process from Django, using SQLAlchemy for database access (primarily read-only; writes impression events for A/B tracking)
  - Thread-safe LRU model cache (configurable max size via `INFERENCE_MAX_LOADED_MODELS`)
  - Redis Pub/Sub listener for hot-swap model updates on channel `recotem:model_events`
  - Rate limiting per API key via `slowapi`
  - Model file integrity verification via HMAC-SHA256 (shared `SECRET_KEY`)
  - API key authentication compatible with Django's PBKDF2-SHA256 hashing (via `passlib`)

### 7. Proxy (`proxy`)

- **Build**: Multi-stage -- builds Vue 3 SPA then copies into nginx image
- **Exposed port**: 8000 (mapped to host)
- **User**: `nginx` (non-root)
- **Memory**: 64 MB reserved, 256 MB limit
- **Routing rules**:

  | Path | Destination | Rate Limit |
  |---|---|---|
  | `/` | Vue 3 SPA (static files) | -- |
  | `/api/` | `backend:8080/api/` | 30 req/s burst 20 |
  | `/api/auth/login/` | `backend:8080/api/auth/login/` | 5 req/min burst 3 |
  | `/ws/` | `backend:8080/ws/` (WebSocket upgrade) | -- |
  | `/admin/` | `backend:8080/admin/` | -- |
  | `/inference/` | `inference:8081/` | 30 req/s burst 50 |
  | `/static/` | `/app/dist/static/` (file system) | -- |

- **Security headers**: X-Frame-Options (DENY), CSP, HSTS, X-Content-Type-Options, Referrer-Policy, Permissions-Policy
- **Compression**: gzip for text, CSS, JS, JSON, SVG

## Technology Stack

### Backend

| Component | Technology | Version |
|---|---|---|
| Language | Python | 3.12 |
| Web framework | Django | 5.1 |
| REST API | Django REST Framework | -- |
| ASGI server | Daphne | -- |
| WebSocket | Django Channels + channels-redis | -- |
| Authentication | dj-rest-auth + simplejwt | -- |
| Task queue | Celery | -- |
| Task scheduler | django-celery-beat | -- |
| ML / Tuning | irspack 0.4.0 + Optuna | -- |
| Package manager | uv | -- |
| Linting | Ruff | -- |

### Frontend

| Component | Technology | Version |
|---|---|---|
| Framework | Vue 3 (Composition API) | 3.5 |
| Build tool | Vite | 6 |
| UI components | PrimeVue | 4 |
| CSS | Tailwind CSS | 4 |
| State management | Pinia | -- |
| Data fetching | TanStack Query | -- |
| Language | TypeScript (strict mode) | -- |
| Package manager | npm | -- |

### Inference

| Component | Technology |
|---|---|
| Framework | FastAPI |
| ORM | SQLAlchemy (read + impression writes) |
| Rate limiting | slowapi |
| Password compat | passlib (Django PBKDF2-SHA256) |

## Networking

All services communicate over a single Docker bridge network (`backend-net`). No ports are exposed except `proxy:8000` which is mapped to the host.

```
┌─────────────────────────── backend-net ───────────────────────────┐
│                                                                   │
│  proxy:8000 ──► backend:8080                                      │
│             ──► inference:8081                                     │
│                                                                   │
│  backend:8080 ──► db:5432                                         │
│               ──► redis:6379                                      │
│                                                                   │
│  worker ──► db:5432                                               │
│         ──► redis:6379                                            │
│                                                                   │
│  beat ──► redis:6379                                              │
│                                                                   │
│  inference:8081 ──► db:5432  (read + impression writes)            │
│                 ──► redis:6379/db3  (Pub/Sub subscriber)           │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
                        │
                   port 8000 exposed
                        │
                    ┌────▼────┐
                    │  Host   │
                    └─────────┘
```

## Volumes

| Volume | Mounted To | Services | Purpose |
|---|---|---|---|
| `db-data` | `/var/lib/postgresql/data/pgdata` | db | Persistent database storage |
| `data-location` | `/data` | backend, worker, beat, inference (ro) | Uploaded datasets and trained model files |
| `static-files` | `/app/dist/static` | backend (rw), proxy (ro) | Django Admin static assets (`collectstatic`) |

## Deployment Variants

### Full Production Stack (7 services)

Defined in `compose.yaml`. All services, full capabilities.

### Development (2 services)

Defined in `compose-dev.yaml`. Only PostgreSQL and Redis. Backend, worker, beat, and frontend run locally.

### Inference-Only (3 services)

Defined in `compose-inference.yaml`. Stripped-down deployment with only `db`, `inference`, and `proxy` (using `nginx-inference.conf`). For read-only recommendation serving without the management UI.

## Data Flow

```
1. User creates Project           ──► backend ──► PostgreSQL
2. User uploads TrainingData CSV  ──► backend ──► /data volume + PostgreSQL
3. User creates tuning job        ──► backend ──► Celery (via Redis db0)
4. Workers run Optuna trials      ──► worker  ──► PostgreSQL (Optuna storage)
                                              ──► Redis db1 (WebSocket push)
5. Best config saved              ──► worker  ──► PostgreSQL
6. Model trained                  ──► worker  ──► /data volume (signed model file)
                                              ──► Redis db3 (model_trained event)
7. Inference picks up event       ──► inference ◄── Redis db3 (Pub/Sub)
                                              ──► /data volume (load model)
8. Client calls inference API     ──► proxy ──► inference ──► in-memory model
                                              ──► PostgreSQL (auto impression)
9. Scheduled retrain (optional)   ──► beat ──► Celery ──► worker (repeat 4-7)
```

## Design Decisions

1. **Separate inference service**: Decouples the recommendation serving path from the Django application. The inference service uses SQLAlchemy for database access (primarily reads; writes only impression events for A/B tracking) and has no Django dependency, enabling independent scaling and deployment.

2. **HMAC-signed model files**: All trained model files are signed with HMAC-SHA256 using the application's `SECRET_KEY`. This prevents loading tampered model files. The signing core module (`pickle_signing_core.py`) is Django-independent and shared with the inference service.

3. **Redis database separation**: Four Redis databases isolate different concerns (broker, channels, cache, model events) to prevent key collisions and allow independent monitoring and eviction policies.

4. **Single Docker image for backend/worker/beat**: All three services share the same Docker image (`backend/Dockerfile`), differing only in their entrypoint command. This simplifies builds and ensures code consistency.

5. **WebSocket via query-string JWT**: Browsers cannot send custom headers on WebSocket upgrade requests. JWT tokens are passed as `?token=<access_token>` query parameters and validated by `JwtAuthMiddleware`.

6. **Daphne as ASGI server**: Daphne serves both HTTP and WebSocket protocols, eliminating the need for separate servers. The backend listens on port 8080 internally; nginx proxies to it on port 8000.

7. **Model hot-swap via Pub/Sub**: When a model is trained, the backend publishes a `model_trained` event to Redis db3. The inference service's background listener picks up the event and loads the new model into its LRU cache, enabling zero-downtime model updates.
