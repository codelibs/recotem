# Development Guide

This guide covers how to set up and run Recotem for development.

## Prerequisites

- Docker & Docker Compose
- Node.js 20+ and Yarn (for frontend development)
- Python 3.12+ (for backend development without Docker)

## Development Setup

### Backend Development

The backend uses Django with Celery for async task processing.

```bash
# Start backend services in development mode
docker compose -f compose-dev.yaml build
docker compose -f compose-dev.yaml up
```

This starts:
- PostgreSQL database
- RabbitMQ message queue
- Django development server (with hot reload)
- Celery worker

The API is available at http://localhost:8000

### Frontend Development

The frontend uses Vue.js 2 with Vuetify.

```bash
cd frontend
yarn install
yarn serve
```

The development server runs at http://localhost:8080 and proxies API requests to the backend.

**Note**: Start the backend services first before running the frontend.

## Project Structure

```
recotem/
├── backend/
│   ├── recotem/           # Django project
│   │   ├── api/           # REST API endpoints
│   │   ├── settings.py    # Django settings
│   │   └── ...
│   ├── requirements.txt
│   └── *.dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/    # Vue components
│   │   ├── views/         # Page components
│   │   └── ...
│   ├── e2e/               # Playwright E2E tests
│   ├── tests/             # Jest unit tests
│   └── package.json
├── compose.yaml           # Full stack for CI/production
├── compose-dev.yaml       # Development configuration
├── compose-test.yaml      # Backend test configuration
└── compose-production.yaml # Production configuration
```

## Running Tests

### Backend Tests (Python)

```bash
# Using Docker
docker compose -f compose-test.yaml up --exit-code-from backend

# Or locally (requires Python environment)
cd backend/recotem
pytest --cov=./recotem/
```

### Frontend Unit Tests (Jest)

```bash
cd frontend
yarn test:unit
```

### E2E Tests (Playwright)

E2E tests require the full stack to be running:

```bash
# Start full stack
docker compose up -d

# Run E2E tests
cd frontend
npx playwright install chromium
npx playwright test e2e/
```

## Code Style

### Backend (Python)

- Use [Black](https://black.readthedocs.io/) for formatting
- Use [isort](https://pycqa.github.io/isort/) for import sorting
- Follow PEP 8 guidelines

```bash
# Format code
black backend/
isort backend/
```

### Frontend (JavaScript/Vue)

- Use ESLint for linting
- Use Prettier for formatting

```bash
cd frontend
yarn lint
```

## Docker Compose Files

| File | Purpose |
|------|---------|
| `compose.yaml` | Full stack for CI and local testing |
| `compose-dev.yaml` | Backend development with hot reload |
| `compose-test.yaml` | Backend pytest execution |
| `compose-production.yaml` | Production deployment |

## Environment Variables

See [.env.example](.env.example) for available configuration options.

For development, default values are used. For production, copy `.env.example` to `.env` and configure appropriately.

## Common Issues

### Backend not starting

Check if the database is healthy:
```bash
docker compose logs db
```

### Frontend can't connect to API

Ensure backend is running on port 8000:
```bash
curl http://localhost:8000/api/ping/
```

### Celery tasks not executing

Check Celery worker logs:
```bash
docker compose logs celery_worker
```
