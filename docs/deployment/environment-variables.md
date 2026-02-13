# Environment Variables Reference

## Required (Production)

| Variable | Description | Example |
|----------|-------------|---------|
| `SECRET_KEY` | Django secret key | `django-insecure-...` (generate randomly) |
| `DATABASE_URL` | PostgreSQL connection URL | `postgresql://user:pass@host:5432/recotem` |
| `ALLOWED_HOSTS` | Comma-separated hostnames | `recotem.example.com,www.example.com` |
| `DEFAULT_ADMIN_PASSWORD` | Initial admin password | (set securely) |

## PostgreSQL

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_USER` | — | PostgreSQL username |
| `POSTGRES_PASSWORD` | — | PostgreSQL password |
| `POSTGRES_DB` | — | Database name |
| `PGDATA` | — | Data directory path |

## Django Core

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG` | `true` | Debug mode (`false` for production) |
| `ALLOWED_HOSTS` | `localhost` | Comma-separated allowed hostnames |
| `CORS_ALLOWED_ORIGINS` | (empty) | Cross-origin origins (comma-separated) |
| `CSRF_TRUSTED_ORIGINS` | (empty) | CSRF trusted origins (comma-separated) |

## Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis URL for Celery broker (db 0) |
| `CHANNELS_REDIS_URL` | `redis://localhost:6379/1` | Redis URL for Django Channels WebSocket layer (db 1) |
| `CACHE_REDIS_URL` | `redis://localhost:6379/2` | Redis URL for cache (db 2) |
| `MODEL_EVENTS_REDIS_URL` | `redis://localhost:6379/3` | Redis URL for model event Pub/Sub (db 3) |
| `CACHE_KEY_PREFIX` | `recotem` | Cache key prefix |
| `REDIS_PASSWORD` | (empty) | Redis password (optional, see below) |

### Redis password injection

When `REDIS_PASSWORD` is set, Recotem automatically injects the password into any `redis://` or `rediss://` URL that does not already contain credentials. This applies to all Redis URLs:

- `CELERY_BROKER_URL`
- `CHANNELS_REDIS_URL`
- `CACHE_REDIS_URL`
- `MODEL_EVENTS_REDIS_URL`

For example, if you set:

```env
REDIS_PASSWORD=mysecret
CELERY_BROKER_URL=redis://redis:6379/0
```

Recotem transforms the URL at startup to `redis://:mysecret@redis:6379/0`.

URLs that already contain a password (e.g. `redis://:existingpass@redis:6379/0`) are left unchanged. This allows you to use different passwords for different Redis instances if needed.

**When to use `REDIS_PASSWORD`**: Use it when all three Redis databases share the same Redis server and password. This avoids repeating the password in every URL.

**When to set individual URLs**: If you use separate Redis instances (e.g. managed ElastiCache for broker, a different instance for cache), set credentials directly in each URL and leave `REDIS_PASSWORD` empty.

## Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `ACCESS_TOKEN_LIFETIME` | `300` | JWT access token lifetime in seconds |

## Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `THROTTLE_ANON_RATE` | `20/min` | Anonymous user rate limit |
| `THROTTLE_USER_RATE` | `100/min` | Authenticated user rate limit |
| `THROTTLE_LOGIN_RATE` | `5/min` | Login endpoint rate limit |
| `THROTTLE_RECOMMENDATION_RATE` | `30/min` | Recommendation endpoint rate limit |

## Celery Worker

| Variable | Default | Description |
|----------|---------|-------------|
| `CELERY_TASK_TIME_LIMIT` | `3600` | Hard task timeout (seconds) |
| `CELERY_TASK_SOFT_TIME_LIMIT` | `3480` | Soft task timeout (seconds) |
| `CELERY_RESULT_EXPIRES` | `604800` | How long to keep task results in DB (seconds, default 7 days) |

## Database Connection

| Variable | Default | Description |
|----------|---------|-------------|
| `CONN_MAX_AGE` | `600` | Database connection lifetime in seconds. Set to `0` to close after each request. Default `600` is optimized for ASGI (Daphne) persistent connections. |

## Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Root logger level |
| `DJANGO_LOG_LEVEL` | `WARNING` | Django framework log level |
| `CELERY_LOG_LEVEL` | `INFO` | Celery worker log level |

In production (`DEBUG=false`), logs are output as structured JSON using `python-json-logger`. In development, a simple text format is used.

## Inference Service

These variables configure the standalone FastAPI inference service.

| Variable | Default | Description |
|----------|---------|-------------|
| `INFERENCE_PORT` | `8081` | Port the inference service listens on |
| `INFERENCE_MAX_LOADED_MODELS` | `10` | Maximum number of models in the LRU cache |
| `INFERENCE_RATE_LIMIT` | `100/minute` | Rate limit per API key |
| `INFERENCE_PRELOAD_MODEL_IDS` | (empty) | Comma-separated model IDs to pre-load on startup (e.g., `1,2,3`) |
| `DATABASE_URL` | (required) | PostgreSQL connection string (shared with backend) |
| `SECRET_KEY` | (required) | Must match the backend's secret for HMAC model verification |
| `MODEL_EVENTS_REDIS_URL` | `redis://localhost:6379/3` | Redis Pub/Sub for model update notifications |

## Scheduled Retraining

| Variable | Default | Description |
|----------|---------|-------------|
| `CELERY_BEAT_SCHEDULER` | `django_celery_beat.schedulers:DatabaseScheduler` | Celery Beat scheduler class |

The `beat` service must be running for scheduled retraining. Schedules are configured per-project through the UI or API, not through environment variables.

## Security (HTTPS)

| Variable | Default | Description |
|----------|---------|-------------|
| `SECURE_SSL_REDIRECT` | `true` | Redirect all HTTP to HTTPS (set `false` to disable) |
| `SECURE_HSTS_SECONDS` | `31536000` | HSTS header max-age (production only, 1 year default) |

These settings are only active when `DEBUG=false`. HSTS and SSL redirect should be enabled when serving behind TLS termination.

## Upload Size

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | `524288000` (500MB) | Maximum upload size in bytes. Should match nginx `client_max_body_size` |

## Model Cache

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_CACHE_SIZE` | `8` | Number of trained models in LRU cache |

## Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `MEDIA_ROOT` | `<BASE_DIR>/data` | Path for uploaded files. Set to `/data` in Docker to match the volume mount |
| `RECOTEM_STORAGE_TYPE` | (empty) | Set to `S3` for S3 storage |
| `AWS_ACCESS_KEY_ID` | — | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | — | AWS secret key |
| `AWS_STORAGE_BUCKET_NAME` | — | S3 bucket name |
| `AWS_S3_ENDPOINT_URL` | — | S3 endpoint (for non-AWS S3) |

## Frontend (separate deployment)

Use these variables when the frontend is hosted separately from the backend/proxy:

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_BASE_URL` | `/api/v1` | Base URL for REST API requests |
| `VITE_WS_BASE_URL` | (derived from browser origin) | Base URL for WebSocket connections (e.g. `wss://api.example.com/ws`) |

## Deployment Scenario Examples

### Single-server (Docker Compose)

```env
SECRET_KEY=<generate-a-random-key>
DATABASE_URL=postgresql://recotem_user:password@db:5432/recotem
ALLOWED_HOSTS=recotem.example.com
DEBUG=false
```

### Separate frontend and backend

```env
# Backend
SECRET_KEY=<generate-a-random-key>
DATABASE_URL=postgresql://recotem_user:password@db:5432/recotem
ALLOWED_HOSTS=api.example.com
CORS_ALLOWED_ORIGINS=https://app.example.com
CSRF_TRUSTED_ORIGINS=https://app.example.com
DEBUG=false

# Frontend (build-time)
VITE_API_BASE_URL=https://api.example.com/api/v1
VITE_WS_BASE_URL=wss://api.example.com/ws
```

### Kubernetes with managed services

```env
SECRET_KEY=<generate-a-random-key>
DATABASE_URL=postgresql://recotem_user:password@rds-endpoint:5432/recotem
CELERY_BROKER_URL=redis://elasticache-endpoint:6379/0
CHANNELS_REDIS_URL=redis://elasticache-endpoint:6379/1
CACHE_REDIS_URL=redis://elasticache-endpoint:6379/2
REDIS_PASSWORD=<redis-auth-token>
ALLOWED_HOSTS=recotem.example.com
RECOTEM_STORAGE_TYPE=S3
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_STORAGE_BUCKET_NAME=recotem-data
DEBUG=false
```
