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
