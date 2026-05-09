"""FastAPI route handlers for the Recotem serving layer.

Routes (spec Section 7):
  POST /predict/{name}   — single-user recommendations
  GET  /health           — per-recipe health (ok | degraded)
  GET  /models           — registry entries (header metadata, no key material)
  GET  /metrics          — Prometheus exposition (opt-in; only when prometheus_client
                           is importable)
"""

from __future__ import annotations

import math
import time
import uuid
from typing import TYPE_CHECKING, Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from recotem.serving import metrics as _metrics
from recotem.serving.auth import verify_api_key
from recotem.serving.registry import ModelRegistry

if TYPE_CHECKING:
    from recotem.config import ApiKeyEntry

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PredictRequest(BaseModel):
    user_id: str
    cutoff: Annotated[int, Field(ge=1, le=1000)] = 10


class RecommendationItem(BaseModel):
    item_id: str
    score: float
    # Extra metadata fields are included as additional properties.
    model_config = {"extra": "allow"}


class ModelInfo(BaseModel):
    recipe: str
    trained_at: str | None = None
    best_class: str | None = None
    kid: str


class PredictResponse(BaseModel):
    items: list[RecommendationItem]
    model: ModelInfo
    request_id: str


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def make_router(
    registry: ModelRegistry,
    api_keys: list[ApiKeyEntry],
    metadata_field_deny: list[str] | None = None,
) -> APIRouter:
    """Build and return the main API router.

    Parameters
    ----------
    registry:
        The shared :class:`~recotem.serving.registry.ModelRegistry`.
    api_keys:
        Parsed API key entries from ``ServeConfig``.
    metadata_field_deny:
        Optional list of metadata field names to strip from prediction
        responses after the item-metadata join.
    """
    router = APIRouter()
    _deny_set: frozenset[str] = frozenset(metadata_field_deny or [])

    # ------------------------------------------------------------------
    # Auth dependency (closure over api_keys)
    # ------------------------------------------------------------------

    def _require_auth(request: Request) -> str:
        return verify_api_key(request, api_keys)

    # ------------------------------------------------------------------
    # POST /predict/{name}
    # ------------------------------------------------------------------

    @router.post(
        "/predict/{name}",
        response_model=PredictResponse,
        summary="Get recommendations for a single user",
    )
    def predict(
        name: str,
        body: PredictRequest,
        request: Request,
        kid: str = Depends(_require_auth),
    ) -> Any:
        """Return top-K recommendations for *user_id* using model *name*."""
        request_id = str(uuid.uuid4())
        start = time.monotonic()
        status = "error"

        try:
            entry = registry.get(name)
            if entry is None or entry.recommender is None:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is not loaded or unhealthy",
                        "code": "recipe_unavailable",
                    },
                )
            if entry.last_load_error is not None:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": (
                            f"Recipe '{name}' is unhealthy: {entry.last_load_error}"
                        ),
                        "code": "recipe_unhealthy",
                    },
                )

            structlog.contextvars.bind_contextvars(
                recipe=name, request_id=request_id, kid=kid
            )
            try:
                raw_results: list[tuple[str, float]] = (
                    entry.recommender.get_recommendation_for_known_user_id(
                        body.user_id, body.cutoff
                    )
                )
            except KeyError:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": (
                            f"User '{body.user_id}' was not seen during training"
                        ),
                        "code": "user_not_found",
                    },
                ) from None
            finally:
                structlog.contextvars.clear_contextvars()

            # Build item list, joining metadata if available.
            items: list[dict[str, Any]] = []
            meta_df = entry.metadata_df

            for item_id, score in raw_results:
                item: dict[str, Any] = {"item_id": item_id, "score": float(score)}
                if meta_df is not None:
                    row = _lookup_metadata(meta_df, item_id, _deny_set)
                    item.update(row)
                items.append(item)

            response = PredictResponse(
                items=[RecommendationItem(**it) for it in items],
                model=ModelInfo(
                    recipe=name,
                    trained_at=entry.trained_at,
                    best_class=entry.best_class,
                    kid=entry.kid,
                ),
                request_id=request_id,
            )

            status = "ok"
            return response
        finally:
            _metrics.record_predict(name, status, time.monotonic() - start)

    # ------------------------------------------------------------------
    # GET /health
    # ------------------------------------------------------------------

    @router.get("/health", summary="Per-recipe health status")
    def health() -> dict[str, Any]:
        """Return per-recipe health.  Overall status is ``degraded`` if any
        recipe is unloaded or carries a load error.

        Every recipe found in the recipes directory at startup appears here,
        regardless of whether its artifact loaded — startup-failed recipes
        are inserted as stubs with ``loaded=false`` and an ``error`` string.
        """
        snapshot = registry.health_snapshot()
        overall = "ok"
        for entry_health in snapshot.values():
            if not entry_health.get("loaded", True) or entry_health.get("error"):
                overall = "degraded"
                break
        return {"status": overall, "recipes": snapshot}

    # ------------------------------------------------------------------
    # GET /models
    # ------------------------------------------------------------------

    @router.get("/models", summary="List loaded models")
    def models(
        kid: str = Depends(_require_auth),
    ) -> list[dict[str, Any]]:
        """Return metadata for all currently loaded models.

        Stub entries inserted for recipes whose artifact failed to load at
        startup are excluded — they have no header or class to report.
        Operators see those via ``/health`` instead.
        """
        return [e.models_dict() for e in registry.list() if e.loaded]

    # ------------------------------------------------------------------
    # GET /metrics (opt-in via RECOTEM_METRICS_ENABLED)
    # ------------------------------------------------------------------

    if _metrics.metrics_enabled():

        @router.get("/metrics", summary="Prometheus metrics", include_in_schema=True)
        def metrics() -> Any:
            """Expose Prometheus metrics.

            Requires both ``prometheus_client`` to be installed and
            ``RECOTEM_METRICS_ENABLED`` to be a truthy value at app
            construction time.
            """
            from fastapi.responses import Response

            data, content_type = _metrics.generate_latest()
            return Response(content=data, media_type=content_type)

    return router


# ---------------------------------------------------------------------------
# Metadata join helper
# ---------------------------------------------------------------------------


def _lookup_metadata(
    meta_df: Any,
    item_id: str,
    deny_set: frozenset[str],
) -> dict[str, Any]:
    """Return a flat dict of metadata fields for *item_id*.

    Returns an empty dict if the item is not found or any error occurs.
    """
    try:
        row = meta_df.loc[item_id]
    except KeyError:
        return {}
    return {
        k: (None if isinstance(v, float) and math.isnan(v) else v)
        for k, v in row.to_dict().items()
        if k not in deny_set
    }
