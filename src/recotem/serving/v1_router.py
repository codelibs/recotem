# src/recotem/serving/v1_router.py
"""FastAPI router for the recotem v1 HTTP API.

This module replaces the legacy `routes.py::make_router` after Task 12.
Routes are added incrementally across Tasks 6-11.
"""

from __future__ import annotations

import re

import structlog
from fastapi import APIRouter, Request

from recotem.config import ApiKeyEntry
from recotem.serving.auth import verify_api_key
from recotem.serving.registry import ModelRegistry

logger = structlog.get_logger(__name__)

# Allowed characters for the X-Request-ID echo (preserved from routes.py).
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def make_v1_router(
    registry: ModelRegistry,
    api_keys: list[ApiKeyEntry],
    metadata_field_deny: list[str] | None = None,
) -> APIRouter:
    """Build and return the v1 API router (mounted under `/v1`)."""
    router = APIRouter()
    _deny_set: frozenset[str] = frozenset(
        s.lower() for s in (metadata_field_deny or [])
    )

    def _require_auth(request: Request) -> str:
        return verify_api_key(request, api_keys)

    # Endpoints are appended in subsequent tasks.  Keep the closure
    # variables (`registry`, `_deny_set`, `_require_auth`) live for them.
    # Suppress unused-warning by exposing on the router (no-op).
    router.dependency_overrides_provider = None
    _ = registry, _deny_set, _require_auth
    return router
