# Contributing to Recotem

Thank you for your interest in contributing to Recotem! This guide will help you get started.

## Prerequisites

- Python 3.13+
- Node.js 22+
- Docker and Docker Compose
- Git

## Development Environment Setup

### 1. Clone the repository

```bash
git clone https://github.com/tohtsky/recotem.git
cd recotem
```

### 2. Start infrastructure services

```bash
docker compose -f compose-dev.yaml up -d
```

This starts PostgreSQL and Redis containers for local development.

### 3. Backend setup

```bash
cd backend/recotem
pip install -r ../requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 8000
```

### 4. Celery worker (separate terminal)

```bash
cd backend/recotem
celery -A recotem worker --loglevel=INFO
```

### 5. Frontend setup (separate terminal)

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
        api/              # REST API (views, serializers, services)
        settings.py
      manage.py
    requirements.txt
    Dockerfile
  frontend/
    src/
      api/                # API client (ofetch)
      components/         # Vue components
      composables/        # Vue composables
      layouts/            # Layout components
      pages/              # Page components
      stores/             # Pinia stores
      types/              # TypeScript type definitions
  compose.yaml            # Production Docker Compose
  compose-dev.yaml        # Development Docker Compose
  proxy.dockerfile        # nginx + frontend SPA
  nginx.conf              # nginx configuration
```

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
cd backend/recotem
pytest --cov
```

### Frontend tests

```bash
cd frontend
npm run test:unit
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

## Code Review

All pull requests require at least one review before merging. Reviewers will check:

- Code quality and adherence to project conventions
- Test coverage for new functionality
- Documentation updates where needed
- No security vulnerabilities introduced
