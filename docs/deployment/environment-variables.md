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
| `CACHE_REDIS_URL` | `redis://localhost:6379/2` | Redis URL for cache (db 2) |
| `CACHE_KEY_PREFIX` | `recotem` | Cache key prefix |
| `REDIS_PASSWORD` | (empty) | Redis password (optional) |

If `REDIS_PASSWORD` is set and `CELERY_BROKER_URL` / `CACHE_REDIS_URL` do not include credentials, Recotem injects the password automatically at runtime.

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

## Celery Worker

| Variable | Default | Description |
|----------|---------|-------------|
| `CELERY_TASK_TIME_LIMIT` | `3600` | Hard task timeout (seconds) |
| `CELERY_TASK_SOFT_TIME_LIMIT` | `3480` | Soft task timeout (seconds) |

## Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Root logger level |
| `DJANGO_LOG_LEVEL` | `WARNING` | Django framework log level |
| `CELERY_LOG_LEVEL` | `INFO` | Celery worker log level |

In production (`DEBUG=false`), logs are output as structured JSON using `python-json-logger`. In development, a simple text format is used.

## Security (HTTPS)

| Variable | Default | Description |
|----------|---------|-------------|
| `SECURE_SSL_REDIRECT` | `false` | Redirect all HTTP to HTTPS (production only) |
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

## Storage (S3)

| Variable | Default | Description |
|----------|---------|-------------|
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
CACHE_REDIS_URL=redis://elasticache-endpoint:6379/2
REDIS_PASSWORD=<redis-auth-token>
ALLOWED_HOSTS=recotem.example.com
RECOTEM_STORAGE_TYPE=S3
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_STORAGE_BUCKET_NAME=recotem-data
DEBUG=false
```
