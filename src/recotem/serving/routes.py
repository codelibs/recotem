"""FastAPI route handlers for the Recotem serving layer.

Routes:
  POST /predict/{name}   — single-user recommendations
  GET  /health           — per-recipe health (ok | degraded)
  GET  /models           — registry entries (header metadata, no key material)
  GET  /metrics          — Prometheus exposition (opt-in; only when prometheus_client
                           is importable)
"""

import math
import re
import time
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from recotem.config import ApiKeyEntry
from recotem.serving import metrics as _metrics
from recotem.serving.auth import verify_api_key
from recotem.serving.registry import ModelRegistry

logger = structlog.get_logger(__name__)

# Allowed characters for an echoed X-Request-ID header value (M-4).
# Accepts up to 64 characters of [A-Za-z0-9_-] (UUID-ish identifiers).
# Any header value that does not match is replaced with a fresh UUID4 so
# ANSI escape sequences, log-injection payloads, or oversized strings are
# never echoed back to the client or embedded in structured log fields.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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
        raw_rid = request.headers.get("x-request-id", "")
        request_id = raw_rid if _REQUEST_ID_RE.match(raw_rid) else str(uuid.uuid4())
        # Set on the background Response object for non-predict paths (errors);
        # the success path returns JSONResponse with its own headers dict below.
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
            if entry is None:
                reason = "no_entry"
                logger.warning(
                    "recipe_unavailable",
                    name=name,
                    reason=reason,
                    request_id=request_id,
                )
                status = "unavailable"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is not loaded or unhealthy",
                        "code": "recipe_unavailable",
                    },
                )
            if not entry.loaded or entry.recommender is None:
                reason = "not_loaded" if not entry.loaded else "recommender_none"
                logger.warning(
                    "recipe_unavailable",
                    name=name,
                    reason=reason,
                    last_load_error=entry.last_load_error,
                    request_id=request_id,
                )
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
                # Only unbind the keys this handler bound — do NOT call
                # clear_contextvars() which would also wipe upstream bindings
                # set by middleware (e.g. request-id, correlation-id).
                structlog.contextvars.unbind_contextvars("recipe", "request_id", "kid")

            # Build item list as plain dicts, joining metadata if available.
            # Fast path: use the pre-flattened metadata_index (O(1) dict.get
            # per item, deny filtering and NaN→None already applied at load
            # time).  Fallback to the DataFrame path only for entries that
            # pre-date the index field (e.g. stubs created directly in tests).
            #
            # R-2: Return via JSONResponse(content=...) to bypass the second
            # pydantic serialization pass that FastAPI performs when the route
            # returns a model instance.  response_model=PredictResponse is kept
            # on the decorator for OpenAPI schema generation; FastAPI skips
            # pydantic validation when the return value is a Response subclass.
            #
            # R-3: Re-set item_id and score AFTER metadata update so that a
            # metadata column named "item_id" or "score" cannot shadow the
            # trusted recommender values.
            item_dicts: list[dict[str, Any]] = []
            meta_index = entry.metadata_index
            meta_df = entry.metadata_df if meta_index is None else None
            _meta_failures = 0  # I-11: count per-request metadata lookup failures

            for item_id, score in raw_results:
                fields: dict[str, Any] = {}
                if meta_index is not None:
                    fields.update(meta_index.get(item_id, {}))
                elif meta_df is not None:
                    # Track how many items returned an empty dict due to a
                    # non-KeyError failure in _lookup_metadata (I-11).
                    _before_size = len(fields)
                    row = _lookup_metadata(meta_df, item_id, _deny_set, name)
                    if not row and item_id in meta_df.index:
                        # item_id was in the index but lookup returned empty —
                        # indicates an internal lookup failure (not a missing key).
                        _meta_failures += 1
                    fields.update(row)
                # Overwrite after metadata join: trusted recommender values
                # must not be shadowed by metadata columns with the same name.
                fields["item_id"] = item_id
                fields["score"] = float(score)
                item_dicts.append(fields)

            # name is FastAPI-validated (Path regex), trained_at/best_class/kid
            # are str|None straight from the trusted artifact header and registry.
            content: dict[str, Any] = {
                "items": item_dicts,
                "model": {
                    "recipe": name,
                    "trained_at": entry.trained_at,
                    "best_class": entry.best_class,
                    "kid": entry.kid,
                },
                "request_id": request_id,
            }

            status = "ok"
            # Build response headers: always include X-Request-ID; add the
            # X-Recotem-Metadata-Degraded sentinel when any metadata lookup
            # failed during this request (I-11).
            resp_headers: dict[str, str] = {"X-Request-ID": request_id}
            if _meta_failures > 0:
                resp_headers["X-Recotem-Metadata-Degraded"] = "1"
            # Include X-Request-ID directly in JSONResponse headers so it is
            # present regardless of how FastAPI merges background response
            # headers into returned Response subclasses.
            return JSONResponse(
                content=content,
                headers=resp_headers,
            )
        except HTTPException:
            raise
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            logger.exception(
                "predict_handler_unexpected_error",
                name=name,
                request_id=request_id,
                kid=kid,
                error_class=type(exc).__name__,
            )
            raise
        finally:
            _metrics.record_predict(name, status, time.monotonic() - start)

    # ------------------------------------------------------------------
    # GET /health
    # ------------------------------------------------------------------

    @router.get("/health", summary="Overall health status (probe-safe)")
    def health(response: Response) -> dict[str, Any]:
        """Return aggregate health suitable for k8s readiness/liveness probes.

        Returns only ``{status, total, loaded}`` — no per-recipe detail or
        sensitive key identifiers are included so this endpoint can be called
        without authentication.

        Use ``GET /health/details`` (authenticated) to obtain per-recipe
        breakdowns including ``kid``, ``trained_at``, and ``best_class``.

        HTTP status mirrors ``status``:

        - ``200 OK``         when every recipe is loaded and free of errors.
        - ``503 Service Unavailable`` when any recipe is unloaded or carries
          a ``last_load_error``.  Kubernetes readiness/liveness probes only
          consider the status code, so returning 200 for a degraded process
          would let Pods be marked ``Ready`` while every prediction returns
          503 — defeating the rolling-upgrade safety net.
        """
        snapshot = registry.health_snapshot()
        total = len(snapshot)
        loaded_count = sum(
            1
            for entry_health in snapshot.values()
            if entry_health.get("loaded", False) and not entry_health.get("error")
        )
        overall = (
            "ok"
            if (loaded_count == total and total > 0 or total == 0 and loaded_count == 0)
            else "degraded"
        )
        # Recheck: if any entry is degraded, mark overall degraded.
        for entry_health in snapshot.values():
            if not entry_health.get("loaded", True) or entry_health.get("error"):
                overall = "degraded"
                break
        if overall == "degraded":
            response.status_code = 503
        return {"status": overall, "total": total, "loaded": loaded_count}

    # ------------------------------------------------------------------
    # GET /health/details
    # ------------------------------------------------------------------

    @router.get("/health/details", summary="Per-recipe health detail (authenticated)")
    def health_details(
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> dict[str, Any]:
        """Return per-recipe health detail including ``kid``, ``trained_at``,
        ``best_class``, and load errors.

        Requires authentication (``X-API-Key``) because the per-recipe detail
        includes artifact key identifiers (``kid``) which should not be publicly
        discoverable.  Use ``GET /health`` for unauthenticated probe-safe status.

        Every recipe found in the recipes directory at startup appears here,
        regardless of whether its artifact loaded — startup-failed recipes
        are inserted as stubs with ``loaded=false`` and an ``error`` string.

        HTTP status mirrors the aggregate status:

        - ``200 OK``         when every recipe is loaded and free of errors.
        - ``503 Service Unavailable`` when any recipe is unloaded or carries
          a ``last_load_error``.
        """
        snapshot = registry.health_snapshot()
        overall = "ok"
        for entry_health in snapshot.values():
            if not entry_health.get("loaded", True) or entry_health.get("error"):
                overall = "degraded"
                break
        if overall == "degraded":
            response.status_code = 503
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

        @router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
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
    if item_id not in meta_df.index:
        return {}
    try:
        row = meta_df.loc[item_id]
    except KeyError:
        # Reaching here means item_id passed the index check above but
        # loc[] still raised — possible with a non-unique index returning a
        # DataFrame instead of a Series, or a corrupt index state.
        # Log at WARNING so operators can detect metadata misconfiguration;
        # also increment the metric so this class of error is observable in
        # dashboards alongside other metadata lookup failures.
        logger.warning(
            "metadata_lookup_unexpected_keyerror",
            recipe=recipe_name,
            item_id=str(item_id),
        )
        _metrics.inc_metadata_lookup_error(recipe_name)
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
