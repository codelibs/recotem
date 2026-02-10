# Recotem

## Overview

Recotem is an easy-to-use interface to recommender systems.
Recotem can be launched on any platform with Docker.
It ships with a web-based UI, and you can train and evaluate recommendation engines entirely through the UI.

Recotem is licensed under Apache 2.0.

## Website

[recotem.org](https://recotem.org)

## Issues / Questions

[discuss.codelibs.org](https://discuss.codelibs.org/c/recotemen/11)

## Getting Started

### Quick Start with Docker

```sh
docker compose up
```

This starts 5 services (PostgreSQL, Redis, backend, worker, proxy) and the app is available at `http://localhost:8000`.

> **Important:** Before starting in production, copy `envs/production.env` and change all `CHANGE_ME_*` values to secure passwords and a random secret key.

### Using pre-built images

1. Visit the [latest release](https://github.com/codelibs/recotem/releases/latest)
2. Download "Docker resources to try out" from Assets
3. Unzip and run `docker compose up`

See the [installation guide](https://recotem.org/guide/installation.html) for more details.

## Architecture

```
proxy (nginx + SPA) --> backend (daphne/ASGI) --> PostgreSQL 17
                                               --> Redis 7 <-- worker (celery)
                                                 (broker + cache + channels)
```

- **5 services**: postgres, redis, backend, worker, proxy
- **Backend**: Django REST Framework + daphne (HTTP + WebSocket)
- **Frontend**: Vue 3 + Vite + PrimeVue 4 + Tailwind CSS 4
- **Task queue**: Celery with Redis broker

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for full setup instructions.

### Quick setup

```sh
# Start infrastructure (PostgreSQL + Redis)
docker compose -f compose-dev.yaml up -d

# Backend
cd backend/recotem
pip install -r ../requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 8000

# Celery worker (separate terminal)
cd backend/recotem
celery -A recotem worker --loglevel=INFO

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

The frontend dev server runs at http://localhost:5173 with API proxy to the backend.

### Testing

```sh
# Backend tests
cd backend/recotem
pytest --cov

# Frontend unit tests
cd frontend
npm run test:unit

# Frontend E2E tests
cd frontend
npm run test:e2e
```

## Command-line tool

[recotem-cli](https://github.com/codelibs/recotem-cli) allows you to tune, train, and get recommendations via the command line.

## Batch execution on ECS

See the [example project](https://github.com/codelibs/recotem-batch-example) for batch execution on Amazon ECS.
