# syntax=docker/dockerfile:1
# Dockerfile.recotem — Recotem 2.0 multi-stage image
# Single image carries both `recotem train` and `recotem serve`.
# Base: python:3.12-slim
# Install: uv
# Runtime user: appuser (uid 1000)
# Entrypoint: recotem
# CMD: ["--help"]
#
# Optional extras bundled: bigquery, s3, gcs, metrics
# (az/adlfs is excluded — adlfs pulls in a large Azure SDK; use a custom image
#  if Azure Blob Storage support is needed.)

# ── stage: base ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install system dependencies needed by irspack / scipy / pandas at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user.  UID/GID 1000 matches the spec requirement.
RUN groupadd --gid 1000 appuser \
 && useradd --uid 1000 --gid 1000 --no-create-home --shell /sbin/nologin appuser

# Install uv (universal Python package manager).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_SYSTEM_PYTHON=1 \
    UV_NO_CACHE=1

# ── stage: builder ────────────────────────────────────────────────────────────
FROM base AS builder

WORKDIR /build

# Copy dependency manifests first for layer caching.
COPY pyproject.toml uv.lock ./

# Install all runtime extras (bigquery, s3, gcs, metrics) but NOT az.
# Use --no-dev to exclude test/dev group.
RUN uv sync \
        --no-dev \
        --extra bigquery \
        --extra s3 \
        --extra gcs \
        --extra metrics \
        --frozen

# Copy source tree.
COPY src/ ./src/

# Build and install the recotem wheel into the virtual env.
RUN uv pip install --no-deps \
        --extra bigquery \
        --extra s3 \
        --extra gcs \
        --extra metrics \
        .

# ── stage: runtime ────────────────────────────────────────────────────────────
FROM base AS runtime

# Copy the populated virtual env from builder.
COPY --from=builder /build/.venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

# Default data directories.  Operators bind-mount recipes and artifacts.
RUN mkdir -p /recipes /artifacts \
 && chown -R appuser:appuser /recipes /artifacts

USER appuser

WORKDIR /workspace

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD recotem --version || exit 1

ENTRYPOINT ["recotem"]
CMD ["--help"]
