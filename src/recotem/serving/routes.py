"""FastAPI router for the recotem v1 HTTP API.

The router is mounted at ``/v1`` by ``serving/app.py`` and exposes the
``:recommend``, ``:recommend-related``, ``:batch-recommend``,
``:batch-recommend-related`` colon-verb endpoints alongside the
``/recipes`` discovery, ``/health``, and (optional) ``/metrics`` routes.
"""

from __future__ import annotations

import collections
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response

from recotem.config import ApiKeyEntry
from recotem.serving import metrics as _metrics
from recotem.serving.auth import verify_api_key
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.schemas import (
    BatchRecommendRelatedRequest,
    BatchRecommendRequest,
    BatchRecommendResponse,
    BatchResultEntry,
    ErrorDetail,
    RecipeDetailResponse,
    RecipesListResponse,
    RecommendItem,
    RecommendRelatedRequest,
    RecommendRequest,
    RecommendResponse,
)

logger = structlog.get_logger(__name__)

_METADATA_WARN_LIMIT = 10
_metadata_warn_counter: collections.Counter[tuple[str, str]] = collections.Counter()


def _lookup_metadata(
    meta_df: Any,
    item_id: str,
    deny_set: frozenset[str],
    recipe_name: str = "",
) -> tuple[dict[str, Any], bool]:
    if item_id not in meta_df.index:
        return {}, False
    try:
        row = meta_df.loc[item_id]
    except KeyError:
        key = (recipe_name, "unexpected_keyerror")
        if _metadata_warn_counter[key] < _METADATA_WARN_LIMIT:
            logger.warning(
                "metadata_lookup_unexpected_keyerror",
                recipe=recipe_name,
                item_id=str(item_id)[:64],
            )
        _metadata_warn_counter[key] += 1
        _metrics.inc_metadata_lookup_error(recipe_name)
        return {}, True
    try:
        out: dict[str, Any] = {}
        for k, v in row.to_dict().items():
            if not isinstance(k, str):
                continue
            if k.lower() in deny_set:
                continue
            out[k] = None if isinstance(v, float) and math.isnan(v) else v
        return out, False
    except (AttributeError, TypeError, ValueError) as exc:
        key = (recipe_name, "lookup_failed")
        if _metadata_warn_counter[key] < _METADATA_WARN_LIMIT:
            logger.warning(
                "metadata_lookup_failed",
                recipe=recipe_name,
                item_id=str(item_id)[:64],
                error_class=type(exc).__name__,
            )
        _metadata_warn_counter[key] += 1
        _metrics.inc_metadata_lookup_error(recipe_name)
        return {}, True


def make_router(
    registry: ModelRegistry,
    api_keys: list[ApiKeyEntry],
    metadata_field_deny: list[str] | None = None,
) -> APIRouter:
    router = APIRouter()
    _deny_set: frozenset[str] = frozenset(
        s.lower() for s in (metadata_field_deny or [])
    )

    def _require_auth(request: Request) -> str:
        return verify_api_key(request, api_keys)

    def _resolve_entry(name: str, request_id: str, kid: str) -> ModelEntry:
        entry = registry.get(name)
        if entry is None:
            logger.warning(
                "recipe_unavailable",
                name=name,
                reason="not_found",
                request_id=request_id,
                kid=kid,
            )
            raise HTTPException(
                status_code=404,
                detail={
                    "detail": f"Recipe '{name}' not found",
                    "code": "RECIPE_NOT_FOUND",
                },
            )
        if not entry.loaded or entry.recommender is None:
            logger.warning(
                "recipe_unavailable",
                name=name,
                reason="not_loaded",
                request_id=request_id,
                kid=kid,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "detail": f"Recipe '{name}' is registered but not loaded",
                    "code": "RECIPE_UNAVAILABLE",
                },
            )
        return entry

    @contextmanager
    def _request_metrics(recipe: str, verb: str, kid: str) -> Iterator[list[str]]:
        start = time.monotonic()
        structlog.contextvars.bind_contextvars(recipe=recipe, kid=kid)
        status_holder: list[str] = ["error"]
        try:
            yield status_holder
        finally:
            _metrics.record_v1_request(
                recipe, verb, status_holder[0], time.monotonic() - start
            )
            structlog.contextvars.unbind_contextvars("recipe", "kid")

    @router.get("/health", summary="Overall health status (probe-safe)")
    def health(response: Response) -> dict[str, Any]:
        snapshot = registry.health_snapshot()
        total = len(snapshot)
        loaded_count = registry.loaded_count()
        overall = "ok" if total == 0 or loaded_count == total else "degraded"
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
        structlog.contextvars.bind_contextvars(kid=kid)
        try:
            snapshot = registry.health_snapshot()
            overall = "ok"
            for entry_health in snapshot.values():
                if not entry_health.get("loaded", True) or entry_health.get("error"):
                    overall = "degraded"
                    break
            if overall == "degraded":
                response.status_code = 503
            return {"status": overall, "recipes": snapshot}
        finally:
            structlog.contextvars.unbind_contextvars("kid")

    if _metrics.metrics_enabled():

        @router.get(
            "/metrics",
            summary="Prometheus metrics",
            include_in_schema=False,
        )
        def metrics_endpoint(kid: str = Depends(_require_auth)) -> Any:
            structlog.contextvars.bind_contextvars(kid=kid)
            try:
                data, content_type = _metrics.generate_latest()
                return Response(content=data, media_type=content_type)
            finally:
                structlog.contextvars.unbind_contextvars("kid")

    @router.post(
        "/recipes/{name}:recommend",
        response_model=RecommendResponse,
        summary="Recommend items for a single user",
    )
    def recommend(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")],
        body: RecommendRequest,
        request: Request,
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "recommend"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid)

                try:
                    raw_results: list[tuple[str, float]] = (
                        entry.recommender.get_recommendation_for_known_user_id(
                            body.user_id, body.limit
                        )
                    )
                except KeyError:
                    status_holder[0] = "unknown_user"
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "detail": "user not seen during training",
                            "code": "UNKNOWN_USER",
                        },
                    ) from None

                exclude = (
                    frozenset(body.exclude_items) if body.exclude_items else frozenset()
                )
                meta_index = entry.metadata_index
                meta_df = entry.metadata_df if meta_index is None else None
                items: list[RecommendItem] = []
                metadata_degraded = False

                for item_id, score in raw_results:
                    if item_id in exclude:
                        continue
                    fields: dict[str, Any] = {}
                    if meta_index is not None:
                        fields.update(meta_index.get(item_id, {}))
                    elif meta_df is not None:
                        meta_fields, degraded = _lookup_metadata(
                            meta_df, item_id, _deny_set, name
                        )
                        fields.update(meta_fields)
                        if degraded:
                            metadata_degraded = True
                    fields["item_id"] = item_id
                    fields["score"] = float(score)
                    items.append(RecommendItem.model_validate(fields))

                status_holder[0] = "ok"
                response.headers["X-Recotem-Model-Version"] = entry.model_version
                if metadata_degraded:
                    response.headers["X-Recotem-Metadata-Degraded"] = "1"
                return RecommendResponse(
                    request_id=request_id,
                    recipe=name,
                    model_version=entry.model_version,
                    items=items,
                )
            except HTTPException:
                raise
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                logger.exception(
                    "recommend_unexpected_error",
                    name=name,
                    request_id=request_id,
                    kid=kid,
                    error_class=type(exc).__name__,
                )
                raise

    @router.post(
        "/recipes/{name}:recommend-related",
        response_model=RecommendResponse,
        summary="Recommend items related to a seed list",
    )
    def recommend_related(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")],
        body: RecommendRelatedRequest,
        request: Request,
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "recommend-related"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid)

                raw_results = entry.recommender.get_recommendation_for_new_user(
                    body.seed_items, body.limit
                )

                if not raw_results:
                    status_holder[0] = "unknown_seed_items"
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "detail": "no known seed_items",
                            "code": "UNKNOWN_SEED_ITEMS",
                        },
                    )

                exclude = (
                    frozenset(body.exclude_items) if body.exclude_items else frozenset()
                )
                meta_index = entry.metadata_index
                meta_df = entry.metadata_df if meta_index is None else None
                items: list[RecommendItem] = []
                metadata_degraded = False

                for item_id, score in raw_results:
                    if item_id in exclude:
                        continue
                    fields: dict[str, Any] = {}
                    if meta_index is not None:
                        fields.update(meta_index.get(item_id, {}))
                    elif meta_df is not None:
                        meta_fields, degraded = _lookup_metadata(
                            meta_df, item_id, _deny_set, name
                        )
                        fields.update(meta_fields)
                        if degraded:
                            metadata_degraded = True
                    fields["item_id"] = item_id
                    fields["score"] = float(score)
                    items.append(RecommendItem.model_validate(fields))

                status_holder[0] = "ok"
                response.headers["X-Recotem-Model-Version"] = entry.model_version
                if metadata_degraded:
                    response.headers["X-Recotem-Metadata-Degraded"] = "1"
                return RecommendResponse(
                    request_id=request_id,
                    recipe=name,
                    model_version=entry.model_version,
                    items=items,
                )
            except HTTPException:
                raise
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                logger.exception(
                    "recommend_related_unexpected_error",
                    name=name,
                    request_id=request_id,
                    kid=kid,
                    error_class=type(exc).__name__,
                )
                raise

    @router.post(
        "/recipes/{name}:batch-recommend",
        response_model=BatchRecommendResponse,
        summary="Recommend items for multiple users",
    )
    def batch_recommend(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")],
        body: BatchRecommendRequest,
        request: Request,
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "batch-recommend"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid)

                _metrics.observe_batch_size(name, verb, len(body.requests))

                results: list[BatchResultEntry] = []
                for idx, single in enumerate(body.requests):
                    try:
                        raw = entry.recommender.get_recommendation_for_known_user_id(
                            single.user_id, single.limit
                        )
                        items = [
                            RecommendItem(item_id=item_id, score=float(score))
                            for item_id, score in raw
                        ]
                        results.append(
                            BatchResultEntry(
                                index=idx,
                                status="ok",
                                items=items,
                                error=None,
                            )
                        )
                    except KeyError:
                        results.append(
                            BatchResultEntry(
                                index=idx,
                                status="error",
                                items=None,
                                error=ErrorDetail(
                                    code="UNKNOWN_USER",
                                    message="user not seen during training",
                                ),
                            )
                        )
                    except (MemoryError, RecursionError):
                        raise
                    except Exception:
                        logger.warning(
                            "batch_element_error",
                            recipe=name,
                            idx=idx,
                            exc_type="Exception",
                        )
                        results.append(
                            BatchResultEntry(
                                index=idx,
                                status="error",
                                items=None,
                                error=ErrorDetail(
                                    code="INTERNAL_ERROR",
                                    message="internal error",
                                ),
                            )
                        )

                status_holder[0] = "ok"
                response.headers["X-Recotem-Model-Version"] = entry.model_version
                return BatchRecommendResponse(
                    request_id=request_id,
                    recipe=name,
                    model_version=entry.model_version,
                    results=results,
                )
            except HTTPException:
                raise
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                logger.exception(
                    "batch_recommend_unexpected_error",
                    name=name,
                    request_id=request_id,
                    kid=kid,
                    error_class=type(exc).__name__,
                )
                raise

    @router.post(
        "/recipes/{name}:batch-recommend-related",
        response_model=BatchRecommendResponse,
        summary="Recommend items related to multiple seed lists",
    )
    def batch_recommend_related(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")],
        body: BatchRecommendRelatedRequest,
        request: Request,
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "batch-recommend-related"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid)

                _metrics.observe_batch_size(name, verb, len(body.requests))

                results: list[BatchResultEntry] = []
                for idx, single in enumerate(body.requests):
                    try:
                        raw = entry.recommender.get_recommendation_for_new_user(
                            single.seed_items, single.limit
                        )
                        if not raw:
                            results.append(
                                BatchResultEntry(
                                    index=idx,
                                    status="error",
                                    items=None,
                                    error=ErrorDetail(
                                        code="UNKNOWN_SEED_ITEMS",
                                        message="no known seed_items",
                                    ),
                                )
                            )
                            continue
                        items = [
                            RecommendItem(item_id=item_id, score=float(score))
                            for item_id, score in raw
                        ]
                        results.append(
                            BatchResultEntry(
                                index=idx,
                                status="ok",
                                items=items,
                                error=None,
                            )
                        )
                    except KeyError:
                        results.append(
                            BatchResultEntry(
                                index=idx,
                                status="error",
                                items=None,
                                error=ErrorDetail(
                                    code="UNKNOWN_SEED_ITEMS",
                                    message="no known seed_items",
                                ),
                            )
                        )
                    except (MemoryError, RecursionError):
                        raise
                    except Exception:
                        logger.warning(
                            "batch_element_error",
                            recipe=name,
                            idx=idx,
                            exc_type="Exception",
                        )
                        results.append(
                            BatchResultEntry(
                                index=idx,
                                status="error",
                                items=None,
                                error=ErrorDetail(
                                    code="INTERNAL_ERROR",
                                    message="internal error",
                                ),
                            )
                        )

                status_holder[0] = "ok"
                response.headers["X-Recotem-Model-Version"] = entry.model_version
                return BatchRecommendResponse(
                    request_id=request_id,
                    recipe=name,
                    model_version=entry.model_version,
                    results=results,
                )
            except HTTPException:
                raise
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                logger.exception(
                    "batch_recommend_related_unexpected_error",
                    name=name,
                    request_id=request_id,
                    kid=kid,
                    error_class=type(exc).__name__,
                )
                raise

    @router.get(
        "/recipes",
        response_model=RecipesListResponse,
        summary="List loaded recipes",
    )
    def list_recipes(kid: str = Depends(_require_auth)) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(kid=kid)
        try:
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
        finally:
            structlog.contextvars.unbind_contextvars("kid")

    @router.get(
        "/recipes/{name}",
        response_model=RecipeDetailResponse,
        summary="Get recipe detail",
    )
    def recipe_detail(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")],
        request: Request,
        kid: str = Depends(_require_auth),
    ) -> dict[str, Any]:
        request_id = request.state.request_id
        structlog.contextvars.bind_contextvars(kid=kid)
        try:
            e = registry.get(name)
            if e is None:
                logger.warning(
                    "recipe_unavailable",
                    name=name,
                    reason="not_found",
                    request_id=request_id,
                    kid=kid,
                )
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": f"Recipe '{name}' not found",
                        "code": "RECIPE_NOT_FOUND",
                    },
                )
            if not e.loaded:
                logger.warning(
                    "recipe_unavailable",
                    name=name,
                    reason="not_loaded",
                    request_id=request_id,
                    kid=kid,
                )
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is registered but not loaded",
                        "code": "RECIPE_UNAVAILABLE",
                    },
                )
            hdr = e.header
            return {
                "name": e.name,
                "model_version": e.model_version,
                "loaded_at": e.loaded_at,
                "supported_verbs": e.supported_verbs,
                "kind": e.kind,
                "config_digest": e.config_digest or "",
                "algorithms": e.algorithms or [],
                "best_algorithm": e.best_class or "",
                "trained_at": hdr.get("trained_at"),
                "best_class": hdr.get("best_class"),
                "best_params": hdr.get("best_params"),
                "best_score": hdr.get("best_score"),
                "metric": hdr.get("metric"),
                "cutoff": hdr.get("cutoff"),
                "tuning": hdr.get("tuning"),
                "data_stats": hdr.get("data_stats"),
                "recotem_version": hdr.get("recotem_version"),
                "irspack_version": hdr.get("irspack_version"),
                "recipe_hash": hdr.get("recipe_hash"),
            }
        except HTTPException:
            raise
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            logger.exception(
                "recipe_detail_unexpected_error",
                name=name,
                request_id=request_id,
                kid=kid,
                error_class=type(exc).__name__,
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars("kid")

    return router
