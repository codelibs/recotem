"""FastAPI router for the recotem v1 HTTP API.

The router is mounted at ``/v1`` by ``serving/app.py`` and exposes the
``:recommend``, ``:recommend-related``, ``:batch-recommend``,
``:batch-recommend-related`` colon-verb endpoints alongside the
``/recipes`` discovery, ``/health``, and (optional) ``/metrics`` routes.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse

from recotem.config import ApiKeyEntry
from recotem.serving import metrics as _metrics
from recotem.serving.auth import verify_api_key
from recotem.serving.registry import ModelRegistry
from recotem.serving.schemas import (
    BatchRecommendRelatedRequest,
    BatchRecommendRequest,
    BatchRecommendResponse,
    RecipeDetailResponse,
    RecipesListResponse,
    RecipeSummary,  # noqa: F401  # re-exported for clarity
    RecommendRelatedRequest,
    RecommendRequest,
    RecommendResponse,
)

logger = structlog.get_logger(__name__)

# Allowed characters for the X-Request-ID echo.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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
            # Guard: skip non-string column names so ``.lower()`` cannot raise
            # AttributeError on an int column name.
            if not isinstance(k, str):
                continue
            if k.lower() in deny_set:
                continue
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


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def make_router(
    registry: ModelRegistry,
    api_keys: list[ApiKeyEntry],
    metadata_field_deny: list[str] | None = None,
) -> APIRouter:
    """Build and return the API router (mounted under ``/v1``)."""
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
            "ok" if (loaded_count == total and total > 0 or total == 0) else "degraded"
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

    @router.post(
        "/recipes/{name}:recommend",
        response_model=RecommendResponse,
        summary="Recommend items for a single user",
    )
    def recommend(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        body: RecommendRequest,
        request: Request,
        kid: str = Depends(_require_auth),
    ) -> Any:
        raw_rid = request.headers.get("x-request-id", "")
        request_id = raw_rid if _REQUEST_ID_RE.match(raw_rid) else str(uuid.uuid4())
        start = time.monotonic()
        status = "error"
        verb = "recommend"

        try:
            entry = registry.get(name)
            if entry is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": f"Recipe '{name}' not found",
                        "code": "RECIPE_NOT_FOUND",
                    },
                )
            if not entry.loaded or entry.recommender is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is registered but not loaded",
                        "code": "RECIPE_UNAVAILABLE",
                    },
                )

            try:
                raw_results: list[tuple[str, float]] = (
                    entry.recommender.get_recommendation_for_known_user_id(
                        body.user_id, body.limit
                    )
                )
            except KeyError:
                status = "unknown_user"
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": (
                            f"User '{body.user_id}' was not seen during training"
                        ),
                        "code": "UNKNOWN_USER",
                    },
                ) from None

            items: list[dict[str, Any]] = []
            meta_index = entry.metadata_index
            meta_df = entry.metadata_df if meta_index is None else None
            for item_id, score in raw_results:
                fields: dict[str, Any] = {}
                if meta_index is not None:
                    fields.update(meta_index.get(item_id, {}))
                elif meta_df is not None:
                    fields.update(_lookup_metadata(meta_df, item_id, _deny_set, name))
                fields["item_id"] = item_id
                fields["score"] = float(score)
                items.append(fields)

            status = "ok"
            return JSONResponse(
                content={
                    "request_id": request_id,
                    "recipe": name,
                    "model_version": entry.model_version,
                    "items": items,
                },
                headers={
                    "X-Recotem-Model-Version": entry.model_version,
                },
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "recommend_unexpected_error",
                name=name,
                request_id=request_id,
                kid=kid,
            )
            raise
        finally:
            _metrics.record_v1_request(name, verb, status, time.monotonic() - start)

    @router.post(
        "/recipes/{name}:recommend-related",
        response_model=RecommendResponse,
        summary="Recommend items related to a seed list",
    )
    def recommend_related(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        body: RecommendRelatedRequest,
        request: Request,
        kid: str = Depends(_require_auth),
    ) -> Any:
        raw_rid = request.headers.get("x-request-id", "")
        request_id = raw_rid if _REQUEST_ID_RE.match(raw_rid) else str(uuid.uuid4())
        start = time.monotonic()
        status = "error"
        verb = "recommend-related"

        try:
            entry = registry.get(name)
            if entry is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": f"Recipe '{name}' not found",
                        "code": "RECIPE_NOT_FOUND",
                    },
                )
            if not entry.loaded or entry.recommender is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is registered but not loaded",
                        "code": "RECIPE_UNAVAILABLE",
                    },
                )

            raw_results = entry.recommender.get_recommendation_for_new_user(
                body.seed_items, body.limit
            )

            if not raw_results:
                status = "unknown_seed_items"
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": (
                            f"None of the seed_items {body.seed_items!r} "
                            "were known to the model"
                        ),
                        "code": "UNKNOWN_SEED_ITEMS",
                    },
                )

            items: list[dict[str, Any]] = []
            meta_index = entry.metadata_index
            meta_df = entry.metadata_df if meta_index is None else None
            for item_id, score in raw_results:
                fields: dict[str, Any] = {}
                if meta_index is not None:
                    fields.update(meta_index.get(item_id, {}))
                elif meta_df is not None:
                    fields.update(_lookup_metadata(meta_df, item_id, _deny_set, name))
                fields["item_id"] = item_id
                fields["score"] = float(score)
                items.append(fields)

            status = "ok"
            return JSONResponse(
                content={
                    "request_id": request_id,
                    "recipe": name,
                    "model_version": entry.model_version,
                    "items": items,
                },
                headers={
                    "X-Recotem-Model-Version": entry.model_version,
                },
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "recommend_related_unexpected_error",
                name=name,
                request_id=request_id,
                kid=kid,
            )
            raise
        finally:
            _metrics.record_v1_request(name, verb, status, time.monotonic() - start)

    @router.post(
        "/recipes/{name}:batch-recommend",
        response_model=BatchRecommendResponse,
        summary="Recommend items for multiple users",
    )
    def batch_recommend(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        body: BatchRecommendRequest,
        request: Request,
        kid: str = Depends(_require_auth),
    ) -> Any:
        raw_rid = request.headers.get("x-request-id", "")
        request_id = raw_rid if _REQUEST_ID_RE.match(raw_rid) else str(uuid.uuid4())
        start = time.monotonic()
        status = "error"
        verb = "batch-recommend"

        try:
            entry = registry.get(name)
            if entry is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": f"Recipe '{name}' not found",
                        "code": "RECIPE_NOT_FOUND",
                    },
                )
            if not entry.loaded or entry.recommender is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is registered but not loaded",
                        "code": "RECIPE_UNAVAILABLE",
                    },
                )

            _metrics.observe_batch_size(name, verb, len(body.requests))

            results: list[dict[str, Any]] = []
            for idx, single in enumerate(body.requests):
                try:
                    raw = entry.recommender.get_recommendation_for_known_user_id(
                        single.user_id, single.limit
                    )
                    items = [
                        {"item_id": item_id, "score": float(score)}
                        for item_id, score in raw
                    ]
                    results.append(
                        {
                            "index": idx,
                            "status": "ok",
                            "items": items,
                            "error": None,
                        }
                    )
                except KeyError:
                    results.append(
                        {
                            "index": idx,
                            "status": "error",
                            "items": None,
                            "error": {
                                "code": "UNKNOWN_USER",
                                "message": (
                                    f"User '{single.user_id}' "
                                    "was not seen during training"
                                ),
                            },
                        }
                    )

            status = "ok"
            return JSONResponse(
                content={
                    "request_id": request_id,
                    "recipe": name,
                    "model_version": entry.model_version,
                    "results": results,
                },
                headers={
                    "X-Recotem-Model-Version": entry.model_version,
                },
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "batch_recommend_unexpected_error",
                name=name,
                request_id=request_id,
                kid=kid,
            )
            raise
        finally:
            _metrics.record_v1_request(name, verb, status, time.monotonic() - start)

    @router.post(
        "/recipes/{name}:batch-recommend-related",
        response_model=BatchRecommendResponse,
        summary="Recommend items related to multiple seed lists",
    )
    def batch_recommend_related(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        body: BatchRecommendRelatedRequest,
        request: Request,
        kid: str = Depends(_require_auth),
    ) -> Any:
        raw_rid = request.headers.get("x-request-id", "")
        request_id = raw_rid if _REQUEST_ID_RE.match(raw_rid) else str(uuid.uuid4())
        start = time.monotonic()
        status = "error"
        verb = "batch-recommend-related"

        try:
            entry = registry.get(name)
            if entry is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": f"Recipe '{name}' not found",
                        "code": "RECIPE_NOT_FOUND",
                    },
                )
            if not entry.loaded or entry.recommender is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is registered but not loaded",
                        "code": "RECIPE_UNAVAILABLE",
                    },
                )

            _metrics.observe_batch_size(name, verb, len(body.requests))

            results: list[dict[str, Any]] = []
            for idx, single in enumerate(body.requests):
                raw = entry.recommender.get_recommendation_for_new_user(
                    single.seed_items, single.limit
                )
                if not raw:
                    results.append(
                        {
                            "index": idx,
                            "status": "error",
                            "items": None,
                            "error": {
                                "code": "UNKNOWN_SEED_ITEMS",
                                "message": (
                                    f"None of the seed_items "
                                    f"{single.seed_items!r} were known to the model"
                                ),
                            },
                        }
                    )
                    continue
                items = [
                    {"item_id": item_id, "score": float(score)}
                    for item_id, score in raw
                ]
                results.append(
                    {
                        "index": idx,
                        "status": "ok",
                        "items": items,
                        "error": None,
                    }
                )

            status = "ok"
            return JSONResponse(
                content={
                    "request_id": request_id,
                    "recipe": name,
                    "model_version": entry.model_version,
                    "results": results,
                },
                headers={
                    "X-Recotem-Model-Version": entry.model_version,
                },
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "batch_recommend_related_unexpected_error",
                name=name,
                request_id=request_id,
                kid=kid,
            )
            raise
        finally:
            _metrics.record_v1_request(name, verb, status, time.monotonic() - start)

    @router.get(
        "/recipes",
        response_model=RecipesListResponse,
        summary="List loaded recipes",
    )
    def list_recipes(kid: str = Depends(_require_auth)) -> dict[str, Any]:
        summaries: list[dict[str, Any]] = []
        for e in registry.list():
            if not e.loaded:
                continue
            summaries.append(
                {
                    "name": e.name,
                    "model_version": e.model_version,
                    "loaded_at": e.loaded_at,
                    "supported_verbs": e.supported_verbs,
                    "kind": e.kind,
                }
            )
        return {"recipes": summaries}

    @router.get(
        "/recipes/{name}",
        response_model=RecipeDetailResponse,
        summary="Get recipe detail",
    )
    def recipe_detail(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        kid: str = Depends(_require_auth),
    ) -> dict[str, Any]:
        e = registry.get(name)
        if e is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "detail": f"Recipe '{name}' not found",
                    "code": "RECIPE_NOT_FOUND",
                },
            )
        if not e.loaded:
            raise HTTPException(
                status_code=503,
                detail={
                    "detail": f"Recipe '{name}' is registered but not loaded",
                    "code": "RECIPE_UNAVAILABLE",
                },
            )
        return {
            "name": e.name,
            "model_version": e.model_version,
            "loaded_at": e.loaded_at,
            "supported_verbs": e.supported_verbs,
            "kind": e.kind,
            "config_digest": e.config_digest or "",
            "algorithms": e.algorithms or [],
            "best_algorithm": e.best_class or "",
        }

    return router
