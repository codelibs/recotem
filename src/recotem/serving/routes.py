"""FastAPI route handlers for the Recotem serving layer.

Routes:
  POST /predict/{name}   — single-user recommendations
  GET  /health           — per-recipe health (ok | degraded)
  GET  /models           — registry entries (header metadata, no key material)
  GET  /metrics          — Prometheus exposition (opt-in; only when prometheus_client
                           is importable)
"""

import math
import time
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field

from recotem.config import ApiKeyEntry
from recotem.serving import metrics as _metrics
from recotem.serving.auth import verify_api_key
from recotem.serving.registry import ModelRegistry

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
    # Case-fold all deny entries so the comparison is case-insensitive.
    # e.g. "internal_id" in the deny list also blocks "Internal_ID" in metadata.
    _deny_set: frozenset[str] = frozenset(
        s.lower() for s in (metadata_field_deny or [])
    )

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
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        body: PredictRequest,
        request: Request,
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> Any:
        """Return top-K recommendations for *user_id* using model *name*.

        The ``X-Request-ID`` response header is set to the request ID used
        internally (echoed from the incoming ``X-Request-ID`` header when
        present, otherwise a freshly generated UUID4).

        Status labels recorded via :func:`~recotem.serving.metrics.record_predict`:

        - ``ok``            — successful recommendation
        - ``user_not_found`` — user was not in training data (HTTP 404)
        - ``unavailable``   — recipe not loaded or unhealthy (HTTP 503)
        - ``error``         — any other unexpected exception
        """
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        response.headers["X-Request-ID"] = request_id
        start = time.monotonic()
        status = "error"

        try:
            entry = registry.get(name)
            # Only refuse predictions when the recipe has no usable model.
            # ``last_load_error`` alone is *not* a 503 condition: when a fresh
            # artifact fails to verify the watcher leaves the previous model
            # loaded and only flags ``last_load_error`` (see watcher._mark_error
            # — "stale-but-loaded keeps serving").  Surfacing that as 503 here
            # would defeat the hot-swap availability contract.
            if entry is None or not entry.loaded or entry.recommender is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is not loaded or unhealthy",
                        "code": "recipe_unavailable",
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
                status = "user_not_found"
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
                    row = _lookup_metadata(meta_df, item_id, _deny_set, name)
                    item.update(row)
                items.append(item)

            predict_response = PredictResponse(
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
            return predict_response
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
    recipe_name: str = "",
) -> dict[str, Any]:
    """Return a flat dict of metadata fields for *item_id*.

    Returns an empty dict if the item is not found or any error occurs.
    The documented error set that returns empty dict:

    - ``KeyError``      — item not in metadata index (normal, not an error).
    - ``AttributeError`` — non-unique index returned a DataFrame instead of a
                           Series so ``.to_dict()`` behaves unexpectedly.
    - ``TypeError``     — a non-string column name caused ``.lower()`` to fail.
    - ``ValueError``    — malformed row data that cannot be iterated.

    All unexpected errors are logged at WARNING level and increment
    ``recotem_metadata_lookup_errors_total`` so operators can detect
    metadata misconfiguration without silencing it completely.
    """
    try:
        row = meta_df.loc[item_id]
    except KeyError:
        return {}
    try:
        out: dict[str, Any] = {}
        for k, v in row.to_dict().items():
            # Guard: skip non-string column names (M-13 — .lower() would raise
            # AttributeError on an int column name).
            if not isinstance(k, str):
                continue
            if k.lower() in deny_set:
                continue
            # Preserve existing NaN → None normalisation.
            out[k] = None if isinstance(v, float) and math.isnan(v) else v
        return out
    except (AttributeError, TypeError, ValueError) as exc:
        logger.warning(
            "metadata_lookup_failed",
            recipe=recipe_name,
            item_id=str(item_id),
            error_class=type(exc).__name__,
        )
        _metrics.inc_metadata_lookup_error(recipe_name)
        return {}
