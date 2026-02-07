# Multi-stage Dockerfile for Recotem backend
# Used for both the backend (daphne) and worker (celery) services

FROM python:3.13-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/
RUN pip install --prefix=/install --no-cache-dir -r /tmp/requirements.txt

# Testing stage: includes test deps and dataset download
FROM base AS testing
COPY --from=builder /install /usr/local
WORKDIR /app
COPY recotem/ /app
RUN pip install --no-cache-dir pytest pytest-celery pytest-cov pytest-django \
    && python -c "from irspack.dataset import MovieLens100KDataManager; MovieLens100KDataManager(force_download=True)"

# Production stage
FROM base
COPY --from=builder /install /usr/local

RUN groupadd -r -g 1000 appuser \
    && useradd -r -u 1000 -g appuser -d /app -s /sbin/nologin appuser \
    && mkdir -p /app /data \
    && chown -R appuser:appuser /app /data

WORKDIR /app
COPY --chown=appuser:appuser recotem/ /app

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:80/api/ping/ || exit 1

EXPOSE 80
CMD ["/app/start.sh"]
