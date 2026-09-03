# syntax=docker/dockerfile:1
# Dockerfile.recotem — Recotem multi-stage image
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

# Remove the pip that python:3.12-slim bundles.  uv performs every install in
# this image, so pip is never invoked -- and it has become a standing source of
# trivy HIGH findings that upgrading can no longer clear.
#
# We previously upgraded it instead (CVE-2025-8869 >=25.3, CVE-2026-1703
# >=26.0, CVE-2026-6357 >=26.1), on the reasoning that upgrading was cheaper
# than uninstalling and preserved `python -m pip` for operators.  That stopped
# working: the findings are now against the packages pip *vendors* --
# pip/_vendor/msgpack 1.1.2 (GHSA-6v7p-g79w-8964) and pip/_vendor/pkg_resources
# from setuptools 70.3.0 (CVE-2025-47273).  pip 26.2.1 is the latest release and
# still vendors both, so there is no version to upgrade to.
#
# The vendored code is unreachable from the application -- recotem runs out of
# /opt/venv, whose pyvenv.cfg sets include-system-site-packages = false -- but
# it is real code shipped in the image, so deleting it is a truer answer than
# adding a .trivyignore entry that would also mask a future genuine pip CVE.
#
# OPERATOR NOTE: `python -m pip` no longer works inside this image.  Change
# dependencies by rebuilding rather than mutating a running container; if pip is
# genuinely needed, `python -m ensurepip` restores it.
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip \
           /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12

# Create a non-root user.  UID/GID 1000 matches the spec requirement.
RUN groupadd --gid 1000 appuser \
 && useradd --uid 1000 --gid 1000 --no-create-home --shell /sbin/nologin appuser

# Install uv (universal Python package manager).
# Pinned to 0.7 to match astral-sh/setup-uv@v8.1.0 used in CI (test.yml).
# Update by bumping the minor version here and in test.yml together.
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv
ENV UV_SYSTEM_PYTHON=1 \
    UV_NO_CACHE=1

# ── stage: builder ────────────────────────────────────────────────────────────
FROM base AS builder

WORKDIR /build

# Copy dependency manifests + LICENSE first for layer caching.
# LICENSE is required because pyproject.toml declares `license = { file = "LICENSE" }`
# and hatchling reads it during the build performed by `uv sync` / `uv pip install`.
COPY pyproject.toml uv.lock LICENSE README.md ./

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
# Dependencies (including extras) are already resolved by `uv sync` above,
# so --no-deps is sufficient — extras only add deps, not new package code.
RUN uv pip install --no-deps .

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

# Default HEALTHCHECK probes the /health endpoint of the serve process.
# This makes sense for the primary serve mode. For one-shot train jobs the
# container exits before any healthcheck fires, so the probe does not cause
# spurious failures. Operators can override with --no-healthcheck or a custom
# HEALTHCHECK in their compose service / k8s liveness probe.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; port=os.environ.get('RECOTEM_PORT','8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3).status==200 else 1)"]

ENTRYPOINT ["recotem"]
CMD ["--help"]
