# src/recotem/serving/v1_router.py
"""FastAPI router for the recotem v1 HTTP API.

This module replaces the legacy `routes.py::make_router` after Task 12.
Routes are added incrementally across Tasks 6-11.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, Response

from recotem.config import ApiKeyEntry
from recotem.serving import metrics as _metrics
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

    @router.get("/health", summary="Overall health status (probe-safe)")
    def health(response: Response) -> dict[str, Any]:
        snapshot = registry.health_snapshot()
        total = len(snapshot)
        loaded_count = sum(
            1
            for entry_health in snapshot.values()
            if entry_health.get("loaded", False) and not entry_health.get("error")
        )
        overall = (
            "ok"
            if (loaded_count == total and total > 0 or total == 0)
            else "degraded"
        )
        for entry_health in snapshot.values():
            if not entry_health.get("loaded", True) or entry_health.get("error"):
                overall = "degraded"
                break
        if overall == "degraded":
            response.status_code = 503
        return {"status": overall, "total": total, "loaded": loaded_count}

    @router.get(
        "/health/details",
        summary="Per-recipe health detail (authenticated)",
    )
    def health_details(
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> dict[str, Any]:
        snapshot = registry.health_snapshot()
        overall = "ok"
        for entry_health in snapshot.values():
            if not entry_health.get("loaded", True) or entry_health.get("error"):
                overall = "degraded"
                break
        if overall == "degraded":
            response.status_code = 503
        return {"status": overall, "recipes": snapshot}

    if _metrics.metrics_enabled():

        @router.get(
            "/metrics",
            summary="Prometheus metrics",
            include_in_schema=False,
        )
        def metrics_endpoint() -> Any:
            data, content_type = _metrics.generate_latest()
            return Response(content=data, media_type=content_type)

    return router
