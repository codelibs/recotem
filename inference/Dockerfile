# Multi-stage Dockerfile for inference service
FROM python:3.12-slim AS base

RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m appuser

WORKDIR /app

FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

COPY inference/ ./inference/

FROM base AS production

COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH"

USER appuser:appuser
EXPOSE 8081

CMD ["uvicorn", "inference.main:app", "--host", "0.0.0.0", "--port", "8081"]
