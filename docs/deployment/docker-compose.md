# Docker Compose Deployment Guide

## Prerequisites

- Docker Engine 24+
- Docker Compose v2

## Quick Start

1. Copy and configure environment:

```bash
cp envs/.env.example envs/production.env
```

2. Edit `envs/production.env` — at minimum, set:
   - `SECRET_KEY` — generate with: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
   - `POSTGRES_PASSWORD` and update `DATABASE_URL` accordingly
   - `DEFAULT_ADMIN_PASSWORD`
   - `ALLOWED_HOSTS` — your domain name(s)

3. Build and start:

```bash
docker compose build
docker compose up -d
```

4. Access at `http://localhost:8000`

## Architecture

```
[Browser] → :8000 → [nginx/proxy]
                       ├── /           → SPA (static files)
                       ├── /api/       → [backend (daphne)]
                       ├── /ws/        → [backend (WebSocket)]
                       └── /admin/     → [backend (Django admin)]
                                           ├── [PostgreSQL]
                                           ├── [Redis]
                                           └── [Celery worker]
```

## Services

| Service | Image | Port | Description |
|---------|-------|------|-------------|
| db | postgres:17-alpine | 5432 (internal) | PostgreSQL database |
| redis | redis:7-alpine | 6379 (internal) | Celery broker, channels, cache |
| backend | recotem/backend | 80 (internal) | Django + Daphne ASGI |
| worker | recotem/backend | — | Celery worker |
| proxy | recotem/proxy | 8000 (exposed) | nginx + Vue SPA |

## Production Checklist

- [ ] Change all default passwords in `production.env`
- [ ] Set `DEBUG=false`
- [ ] Set `ALLOWED_HOSTS` to your domain(s)
- [ ] Configure `CORS_ALLOWED_ORIGINS` if frontend is on a different domain
- [ ] Configure `CSRF_TRUSTED_ORIGINS` for cross-domain setups
- [ ] Set up TLS termination (reverse proxy or load balancer)
- [ ] Configure database backups
- [ ] Mount persistent volumes for data

## TLS/HTTPS Setup

For HTTPS, place an additional reverse proxy (e.g., Traefik, Caddy, or cloud LB) in front of the proxy service that terminates TLS and forwards to port 8000.

## Backup

Database backup:
```bash
docker compose exec db pg_dump -U recotem_user recotem > backup.sql
```

Restore:
```bash
docker compose exec -T db psql -U recotem_user recotem < backup.sql
```

## Resource Limits

Default limits in `compose.yaml`:

| Service | Memory Limit | Memory Reservation |
|---------|-------------|-------------------|
| backend | 2GB | 512MB |
| worker | 4GB | 1GB |

Adjust via `deploy.resources` in `compose.yaml`.

## Logs

View logs:
```bash
docker compose logs -f backend
docker compose logs -f worker
```

Log rotation is configured (50MB max, 5 files for backend/worker).
