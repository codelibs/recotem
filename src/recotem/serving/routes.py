"""FastAPI router for the recotem v1 HTTP API.

The router is mounted at ``/v1`` by ``serving/app.py`` and exposes the
``:recommend``, ``:recommend-related``, ``:batch-recommend``,
``:batch-recommend-related`` colon-verb endpoints alongside the
``/recipes`` discovery, ``/health``, and (optional) ``/metrics`` routes.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import ValidationError

from recotem.config import ApiKeyEntry
from recotem.serving import metrics as _metrics
from recotem.serving.auth import verify_api_key
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.schemas import (
    BATCH_AGGREGATE_LIMIT,
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

# Path regex shared across every endpoint that names a recipe. Must mirror
# ``recotem.recipe.models.Recipe.name`` so that any recipe accepted at load
# time is also routable. The regex is intentionally permissive of leading
# ``_``/``-`` characters because the recipe loader already accepts them.
_RECIPE_NAME_RE = r"^[A-Za-z0-9_-]{1,64}$"
_RecipeName = Annotated[str, Path(pattern=_RECIPE_NAME_RE)]


def make_router(
    registry: ModelRegistry,
    api_keys: list[ApiKeyEntry],
    metadata_field_deny: list[str] | None = None,  # noqa: ARG001
) -> APIRouter:
    router = APIRouter()
    # metadata_field_deny is consumed at artifact-load time when the
    # per-recipe ``metadata_index`` is built (see watcher / app.py). The
    # router no longer carries a runtime fallback path, so the parameter
    # is preserved only for signature compatibility with the existing
    # ``create_app`` wiring.

    def _require_auth(request: Request) -> str:
        return verify_api_key(request, api_keys)

    def _resolve_entry(
        name: str, request_id: str, kid: str, status_holder: list[str]
    ) -> ModelEntry:
        entry = registry.get(name)
        if entry is None:
            status_holder[0] = "recipe_not_found"
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
            status_holder[0] = "unavailable"
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

    def _build_items(
        raw_results: list[tuple[str, float]],
        exclude: frozenset[str],
        meta_index: dict[str, Any] | None,
    ) -> list[RecommendItem]:
        items: list[RecommendItem] = []
        for item_id, score in raw_results:
            if item_id in exclude:
                continue
            fields: dict[str, Any] = {}
            if meta_index is not None:
                fields.update(meta_index.get(item_id, {}))
            fields["item_id"] = item_id
            fields["score"] = float(score)
            items.append(RecommendItem.model_validate(fields))
        return items

    def _any_seed_known(entry: ModelEntry, seed_items: list[str]) -> bool:
        """Return True if at least one seed is known to the model id-map.

        Used to distinguish ``UNKNOWN_SEED_ITEMS`` (no seed in id-map) from
        ``NO_CANDIDATES`` (some seeds known but the ranker produced no
        survivors after its own filtering / score-thresholding).
        """
        try:
            mapper = entry.recommender._mapper
            id_map = mapper.item_id_to_index
        except AttributeError:
            # Defensive: if the recommender layout ever changes we fall
            # back to the conservative "treat as unknown" branch rather
            # than mis-attributing the failure.
            return False
        return any(str(s) in id_map for s in seed_items)

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
        name: _RecipeName,
        body: RecommendRequest,
        request: Request,
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "recommend"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid, status_holder)

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
                items = _build_items(raw_results, exclude, entry.metadata_index)

                status_holder[0] = "ok"
                response.headers["X-Recotem-Model-Version"] = entry.model_version
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
                    exc_type=type(exc).__name__,
                )
                raise

    @router.post(
        "/recipes/{name}:recommend-related",
        response_model=RecommendResponse,
        summary="Recommend items related to a seed list",
    )
    def recommend_related(
        name: _RecipeName,
        body: RecommendRelatedRequest,
        request: Request,
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "recommend-related"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid, status_holder)

                if not _any_seed_known(entry, body.seed_items):
                    status_holder[0] = "unknown_seed_items"
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "detail": "no known seed_items",
                            "code": "UNKNOWN_SEED_ITEMS",
                        },
                    )

                raw_results = entry.recommender.get_recommendation_for_new_user(
                    body.seed_items, body.limit
                )

                if not raw_results:
                    status_holder[0] = "no_candidates"
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "detail": "no candidates produced by ranker",
                            "code": "NO_CANDIDATES",
                        },
                    )

                exclude = (
                    frozenset(body.exclude_items) if body.exclude_items else frozenset()
                )
                items = _build_items(raw_results, exclude, entry.metadata_index)

                status_holder[0] = "ok"
                response.headers["X-Recotem-Model-Version"] = entry.model_version
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
                    exc_type=type(exc).__name__,
                )
                raise

    @router.post(
        "/recipes/{name}:batch-recommend",
        response_model=BatchRecommendResponse,
        summary="Recommend items for multiple users",
    )
    def batch_recommend(
        name: _RecipeName,
        body: BatchRecommendRequest,
        request: Request,
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "batch-recommend"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid, status_holder)

                _metrics.observe_batch_size(name, verb, len(body.requests))

                results: list[BatchResultEntry] = []
                aggregate_limit = 0
                for idx, raw in enumerate(body.requests):
                    if not isinstance(raw, dict):
                        results.append(
                            _batch_error_entry(
                                idx, "VALIDATION_ERROR", "request must be an object"
                            )
                        )
                        _metrics.inc_batch_element_error(name, verb, "VALIDATION_ERROR")
                        continue
                    try:
                        single = RecommendRequest.model_validate(raw)
                    except ValidationError:
                        results.append(
                            _batch_error_entry(
                                idx, "VALIDATION_ERROR", "validation failed"
                            )
                        )
                        _metrics.inc_batch_element_error(name, verb, "VALIDATION_ERROR")
                        continue
                    if aggregate_limit + single.limit > BATCH_AGGREGATE_LIMIT:
                        results.append(
                            _batch_error_entry(
                                idx,
                                "VALIDATION_ERROR",
                                f"aggregate limit cap exceeded: "
                                f"{BATCH_AGGREGATE_LIMIT}",
                            )
                        )
                        _metrics.inc_batch_element_error(name, verb, "VALIDATION_ERROR")
                        continue
                    aggregate_limit += single.limit
                    try:
                        raw_results = (
                            entry.recommender.get_recommendation_for_known_user_id(
                                single.user_id, single.limit
                            )
                        )
                        exclude = (
                            frozenset(single.exclude_items)
                            if single.exclude_items
                            else frozenset()
                        )
                        items = _build_items(raw_results, exclude, None)
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
                            _batch_error_entry(
                                idx, "UNKNOWN_USER", "user not seen during training"
                            )
                        )
                        _metrics.inc_batch_element_error(name, verb, "UNKNOWN_USER")
                    except (MemoryError, RecursionError):
                        raise
                    except Exception as exc:
                        logger.exception(
                            "batch_element_error",
                            recipe=name,
                            verb=verb,
                            idx=idx,
                            request_id=request_id,
                            kid=kid,
                            exc_type=type(exc).__name__,
                            exc_module=type(exc).__module__,
                        )
                        results.append(
                            _batch_error_entry(idx, "INTERNAL_ERROR", "internal error")
                        )
                        _metrics.inc_batch_element_error(name, verb, "INTERNAL_ERROR")

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
                    exc_type=type(exc).__name__,
                )
                raise

    @router.post(
        "/recipes/{name}:batch-recommend-related",
        response_model=BatchRecommendResponse,
        summary="Recommend items related to multiple seed lists",
    )
    def batch_recommend_related(
        name: _RecipeName,
        body: BatchRecommendRelatedRequest,
        request: Request,
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "batch-recommend-related"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid, status_holder)

                _metrics.observe_batch_size(name, verb, len(body.requests))

                results: list[BatchResultEntry] = []
                aggregate_limit = 0
                for idx, raw in enumerate(body.requests):
                    if not isinstance(raw, dict):
                        results.append(
                            _batch_error_entry(
                                idx, "VALIDATION_ERROR", "request must be an object"
                            )
                        )
                        _metrics.inc_batch_element_error(name, verb, "VALIDATION_ERROR")
                        continue
                    try:
                        single = RecommendRelatedRequest.model_validate(raw)
                    except ValidationError:
                        results.append(
                            _batch_error_entry(
                                idx, "VALIDATION_ERROR", "validation failed"
                            )
                        )
                        _metrics.inc_batch_element_error(name, verb, "VALIDATION_ERROR")
                        continue
                    if aggregate_limit + single.limit > BATCH_AGGREGATE_LIMIT:
                        results.append(
                            _batch_error_entry(
                                idx,
                                "VALIDATION_ERROR",
                                f"aggregate limit cap exceeded: "
                                f"{BATCH_AGGREGATE_LIMIT}",
                            )
                        )
                        _metrics.inc_batch_element_error(name, verb, "VALIDATION_ERROR")
                        continue
                    aggregate_limit += single.limit
                    try:
                        if not _any_seed_known(entry, single.seed_items):
                            results.append(
                                _batch_error_entry(
                                    idx,
                                    "UNKNOWN_SEED_ITEMS",
                                    "no known seed_items",
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "UNKNOWN_SEED_ITEMS"
                            )
                            continue
                        raw_results = entry.recommender.get_recommendation_for_new_user(
                            single.seed_items, single.limit
                        )
                        if not raw_results:
                            results.append(
                                _batch_error_entry(
                                    idx,
                                    "NO_CANDIDATES",
                                    "no candidates produced by ranker",
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "NO_CANDIDATES"
                            )
                            continue
                        exclude = (
                            frozenset(single.exclude_items)
                            if single.exclude_items
                            else frozenset()
                        )
                        items = _build_items(raw_results, exclude, None)
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
                            _batch_error_entry(
                                idx, "UNKNOWN_SEED_ITEMS", "no known seed_items"
                            )
                        )
                        _metrics.inc_batch_element_error(
                            name, verb, "UNKNOWN_SEED_ITEMS"
                        )
                    except (MemoryError, RecursionError):
                        raise
                    except Exception as exc:
                        logger.exception(
                            "batch_element_error",
                            recipe=name,
                            verb=verb,
                            idx=idx,
                            request_id=request_id,
                            kid=kid,
                            exc_type=type(exc).__name__,
                            exc_module=type(exc).__module__,
                        )
                        results.append(
                            _batch_error_entry(idx, "INTERNAL_ERROR", "internal error")
                        )
                        _metrics.inc_batch_element_error(name, verb, "INTERNAL_ERROR")

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
                    exc_type=type(exc).__name__,
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
        name: _RecipeName,
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
                exc_type=type(exc).__name__,
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars("kid")

    return router


def _batch_error_entry(idx: int, code: str, message: str) -> BatchResultEntry:
    return BatchResultEntry(
        index=idx,
        status="error",
        items=None,
        error=ErrorDetail(code=code, message=message),  # type: ignore[arg-type]
    )
