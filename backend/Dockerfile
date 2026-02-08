# Multi-stage Dockerfile for Recotem backend
# Used for both the backend (daphne) and worker (celery) services

FROM python:3.12-slim AS base

LABEL org.opencontainers.image.source="https://github.com/tohtsky/recotem"
LABEL org.opencontainers.image.description="Recotem recommendation system backend"
LABEL org.opencontainers.image.licenses="MIT"
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Testing stage: includes test deps and dataset download
FROM base AS testing
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
COPY recotem/ /app
RUN uv run python -c "from irspack.dataset import MovieLens100KDataManager; MovieLens100KDataManager(force_download=True)"

# Production stage
FROM base
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

RUN groupadd -r -g 1000 appuser \
    && useradd -r -u 1000 -g appuser -d /app -s /sbin/nologin appuser \
    && mkdir -p /app /data \
    && chown -R appuser:appuser /app /data

WORKDIR /app
COPY --chown=appuser:appuser recotem/ /app

USER appuser

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:80/api/ping/ || exit 1

EXPOSE 80
CMD ["/app/start.sh"]
