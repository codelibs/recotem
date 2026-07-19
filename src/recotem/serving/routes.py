"""FastAPI router for the recotem v1 HTTP API.

The router is mounted at ``/v1`` by ``serving/app.py`` and exposes the
``:recommend``, ``:recommend-related``, ``:batch-recommend``,
``:batch-recommend-related`` colon-verb endpoints alongside the
``/recipes`` discovery, ``/health``, and (optional) ``/metrics`` routes.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import ValidationError

from recotem._idmap import ColdStartNumericalError
from recotem.config import ApiKeyEntry
from recotem.serving import metrics as _metrics
from recotem.serving.auth import verify_api_key
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.schemas import (
    BATCH_AGGREGATE_LIMIT,
    BATCH_COLD_SEED_SOLVE_LIMIT,
    BatchRecommendRelatedRequest,
    BatchRecommendRequest,
    BatchRecommendResponse,
    BatchResultErr,
    BatchResultOk,
    ErrorCode,
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

# ---------------------------------------------------------------------------
# Batch validation helpers
# ---------------------------------------------------------------------------

# Maximum number of per-error entries included in sanitized_errors log field.
_BATCH_VALIDATION_MAX_ERRORS = 10


def _sanitize_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Return a sanitized list of pydantic error dicts (loc, msg, type only).

    Strips ``input`` and ``url`` fields (user-controlled / verbose).
    Caps to ``_BATCH_VALIDATION_MAX_ERRORS`` entries to bound log size.
    """
    out: list[dict[str, Any]] = []
    for err in exc.errors()[:_BATCH_VALIDATION_MAX_ERRORS]:
        out.append(
            {
                "loc": err.get("loc", ()),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    return out


def _format_batch_validation_message(exc: ValidationError) -> str:
    """Build a human-readable message from the first pydantic error.

    Format: ``"<dot-joined loc>: <msg>"``.  Falls back to ``"validation
    failed"`` when the error list is empty (should not happen in practice).
    """
    errors = exc.errors()
    if not errors:
        return "validation failed"
    first = errors[0]
    loc_parts = first.get("loc", ())
    loc_path = ".".join(str(p) for p in loc_parts) if loc_parts else ""
    msg = first.get("msg", "validation failed")
    return f"{loc_path}: {msg}" if loc_path else msg


def _cold_seed_solve_bound(body: RecommendRelatedRequest) -> int:
    """Upper bound on the cold-seed CG solves *body* can drive.

    A seed is solved only when it is BOTH absent from the model's id-map and
    named in ``item_features`` (``_idmap.get_recommendation_for_cold_seeds``).
    This deliberately does not consult the id-map, so the bound over-counts a
    seed that turns out to be known -- the same posture as
    ``BATCH_AGGREGATE_LIMIT``, which bounds work by the request's declared
    ``limit`` rather than by the items actually returned. Keeping the cap a
    pure function of the request makes it a stable client contract: the same
    body is accepted or rejected identically regardless of which model happens
    to be loaded, and a retrain that warms a seed cannot silently flip the
    verdict.
    """
    if not body.item_features:
        return 0
    return sum(1 for seed in body.seed_items if str(seed) in body.item_features)


@contextmanager
def _bind_batch_idx(idx: int) -> Iterator[None]:
    """Bind ``idx`` as a structlog contextvar for one batch-loop iteration.

    The shared ``_resolve_recommend`` / ``_resolve_recommend_related``
    resolvers have no batch-index parameter (they serve both the single
    verbs and the batch element loops), so any ``logger.warning`` /
    ``logger.exception`` call made from *inside* a resolver -- e.g.
    ``recommender_layout_unexpected`` or ``recommender_unexpected_key_error``
    -- would otherwise have no way to identify which batch element it came
    from. Binding ``idx`` here lets it ride along on every log event emitted
    anywhere during this element's processing via
    ``structlog.contextvars.merge_contextvars``, without threading an index
    parameter through the resolver signatures.

    Unbound in ``finally`` so neither an exception propagating out nor a
    ``continue`` to the next loop iteration leaves a stale ``idx`` bound for
    the next element.
    """
    structlog.contextvars.bind_contextvars(idx=idx)
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars("idx")


# ---------------------------------------------------------------------------
# Cold-start resolution -- shared by the single and batch verbs
# ---------------------------------------------------------------------------
#
# ``_resolve_recommend`` / ``_resolve_recommend_related`` hold the ONLY copy
# of the case A/B/C branch logic (see docstrings below for the case table).
# Both the single handlers and the batch element loops call these; the
# single handlers map the domain exceptions to an ``HTTPException``, the
# batch loops map them to a ``BatchResultErr``. A bare ``KeyError`` /
# ``AttributeError`` propagating out (already logged here where it
# originates) means "unexpected recommender layout" -- both kinds of caller
# must map that to INTERNAL_ERROR too.


def _has_undeclared_columns(state: dict, values: dict[str, Any]) -> bool:
    """True when *values* carries a key the encoder will never read.

    ``_features._row_values`` drives the encode from ``state["columns"]`` and
    does ``values.get(name)``, so any request key outside that list is
    silently dropped -- the request degrades toward a bias-only profile and
    still returns 200. This is the only detection point for that.

    Derived here rather than reported by ``encode_one`` because the answer is
    a pure function of two things this caller already holds: the request's own
    mapping, and the recipe's declared column names. The declared names come
    from ``_features.state_descriptor`` rather than from ``state["columns"]``
    directly so the state dict's internal shape stays private to
    ``_features``. One coupling remains and is deliberate: if ``_row_values``
    ever reads a key NOT among the declared columns (e.g. a derived column),
    this would misreport it as undeclared. Returning the undeclared keys from
    ``encode_one`` itself would make that unrepresentable.

    Only called on a path where the encode already SUCCEEDED, so *state* is
    non-None and well-formed by construction (``encode_one`` just consumed
    it); no defensive guard is warranted here.
    """
    from recotem._features import state_descriptor  # noqa: PLC0415

    descriptor = state_descriptor(state)
    assert descriptor is not None  # noqa: S101 — non-None on the encoded path
    return not set(values).issubset(descriptor["columns"])


class _ColdStartUnsupported(Exception):
    """The model carries no feature state for the side the request supplied,
    or its search winner is not on the feature-capable allow-list (Task 11's
    ``_require_capability``) despite carrying non-None state."""


class _ColdStartValueUnusable(Exception):
    """A supplied feature value made irspack's cold-start solver numerically
    unstable (``_idmap.ColdStartNumericalError`` -- e.g. an
    extreme-but-finite ``numerical`` value like ``1e22``).

    Deliberately a DIFFERENT error from ``_ColdStartUnsupported``: that one
    means the model/feature side can never do cold start at all, whereas
    this is a per-value condition on an otherwise-capable model. Folding it
    into ``FEATURES_NOT_SUPPORTED`` would incorrectly tell the client the
    model cannot serve this recipe's cold start when a different value
    would have worked fine.
    """


class _NoUsableUser(Exception):
    """Unknown user and no user_features to fall back on."""


class _NoUsableSeeds(Exception):
    """No seed is known and none carried item_features."""


class _NoCandidates(Exception):
    """All seeds known, no features supplied, but the ranker produced no
    survivors after its own filtering/score-thresholding."""


def _resolve_recommend(
    entry: ModelEntry, name: str, verb: str, body: RecommendRequest
) -> list[tuple[str, float]]:
    """Resolve one ``:recommend`` request -- single call or batch element.

    Raises ``_NoUsableUser`` / ``_ColdStartUnsupported`` for conditions the
    caller maps to its own error shape. A propagated ``KeyError`` (logged
    here) means the recommender layout was unexpected -- callers must map
    that to INTERNAL_ERROR.
    """
    # S1: determine known-membership BEFORE calling irspack so a genuine
    # missing user produces UNKNOWN_USER, not INTERNAL_ERROR. user_known is
    # None when the recommender layout is unexpected (F4); irspack itself
    # is still given the chance to serve the request in that case.
    try:
        user_known: bool | None = (
            body.user_id in entry.recommender._mapper.user_id_to_index
        )
    except AttributeError as _attr_exc:
        logger.warning(
            "recommender_layout_unexpected",
            recipe=name,
            verb=verb,
            exc_type=type(_attr_exc).__name__,
        )
        _metrics.inc_recommender_layout_unexpected(name)
        user_known = None

    if user_known is False and body.user_features is not None:
        # Case A: unknown user, cold-started from supplied feature values
        # alone (no interaction history exists yet). The ValueError from
        # get_recommendation_for_cold_user covers BOTH "model has no user
        # feature state" and "the search winner is not feature-capable
        # despite carrying state" (Task 9 persists feature state
        # unconditionally, so a TopPop artifact can have non-None state
        # without being able to act on it) -- Task 11's _require_capability
        # is the single source of truth for that distinction, so we relay
        # it as _ColdStartUnsupported rather than re-deriving the same
        # check here from `user_feature_state is None` alone.
        try:
            raw_results, unknown_columns = (
                entry.recommender.get_recommendation_for_cold_user(
                    body.user_features,
                    cutoff=body.limit,
                )
            )
        except ColdStartNumericalError as exc:
            raise _ColdStartValueUnusable(str(exc)) from None
        except ValueError as exc:
            raise _ColdStartUnsupported(str(exc)) from None
        _metrics.inc_cold_start_request(name, "features_only")
        for column in unknown_columns:
            _metrics.inc_feature_unknown_value(name, "user", column)
        if _has_undeclared_columns(
            entry.recommender.user_feature_state, body.user_features
        ):
            _metrics.inc_feature_unknown_column(name, "user")
        return raw_results

    # Known users always take this path, and a known user's supplied
    # user_features are deliberately IGNORED here, not rejected: the
    # learned embedding was fit to their real interactions and strictly
    # dominates a profile prior, so rejecting would break the natural
    # client pattern of always sending the profile and letting the server
    # decide. Cross-referenced from docs/api-reference.md#feature-aware-cold-start
    # ("A known `user_id` with `user_features` supplied is not an error.").
    try:
        return entry.recommender.get_recommendation_for_known_user_id(
            body.user_id, body.limit
        )
    except KeyError:
        if user_known is False:
            # Deterministic miss: user was not in the id-map, and no
            # user_features were supplied to cold-start.
            raise _NoUsableUser(body.user_id) from None
        # user_known is True or None (unexpected layout): propagate as
        # INTERNAL_ERROR so layout surprises are visible, not silent.
        logger.exception(
            "recommender_unexpected_key_error",
            recipe=name,
            verb=verb,
            user_id_hash=hashlib.sha256(body.user_id.encode()).hexdigest()[:8],
        )
        raise


def _resolve_recommend_related(
    entry: ModelEntry, name: str, verb: str, body: RecommendRelatedRequest
) -> list[tuple[str, float]]:
    """Resolve one ``:recommend-related`` request -- single call or batch
    element.

    Raises ``_NoUsableSeeds`` / ``_ColdStartUnsupported`` / ``_NoCandidates``
    for conditions the caller maps to its own error shape. A propagated
    ``AttributeError`` or ``KeyError`` (logged here) means the recommender
    layout was unexpected -- callers must map that to INTERNAL_ERROR.
    """
    # Fetch the item id-map once, up front: needed both to find cold seeds
    # (case C) and for the plain "any seed known" check.
    try:
        id_map = entry.recommender._mapper.item_id_to_index
    except AttributeError as _attr_exc:
        logger.warning(
            "recommender_layout_unexpected",
            recipe=name,
            verb=verb,
            exc_type=type(_attr_exc).__name__,
        )
        _metrics.inc_recommender_layout_unexpected(name)
        raise

    cold_seeds = [s for s in body.seed_items if str(s) not in id_map]
    have_cold_features = bool(body.item_features) and any(
        str(s) in body.item_features for s in cold_seeds
    )

    if have_cold_features:
        # Case C. Must win over case B: a cold seed has no row in the seed
        # interaction matrix, so the case-B solve
        # (get_recommendation_for_new_user) would silently drop it even
        # though user_features may also be present.
        try:
            raw_results, unknown_columns = (
                entry.recommender.get_recommendation_for_cold_seeds(
                    body.seed_items,
                    body.item_features or {},
                    cutoff=body.limit,
                )
            )
        except ColdStartNumericalError as exc:
            raise _ColdStartValueUnusable(str(exc)) from None
        except ValueError as exc:
            raise _ColdStartUnsupported(str(exc)) from None
        except KeyError:
            # No seed was usable: none known, and none of the cold ones
            # actually carried a feature entry (the `have_cold_features`
            # gate above only requires ONE match; defence in depth in case
            # that gate and this method's own criteria for "usable" ever
            # diverge).
            raise _NoUsableSeeds(list(body.seed_items)) from None
        _metrics.inc_cold_start_request(name, "cold_seeds")
        for column in unknown_columns:
            _metrics.inc_feature_unknown_value(name, "item", column)
        # Only the seeds irspack actually encoded: a KNOWN seed contributes
        # its learned embedding and its item_features entry is never looked
        # at (see _idmap.get_recommendation_for_cold_seeds), so a typo in
        # that entry is not a degradation and must not be counted. This
        # mirrors that loop's own "cold AND carries features" criterion using
        # the cold_seeds list already computed above for the case-C gate.
        supplied = body.item_features or {}
        if any(
            _has_undeclared_columns(
                entry.recommender.item_feature_state, supplied[str(seed)]
            )
            for seed in cold_seeds
            if str(seed) in supplied
        ):
            _metrics.inc_feature_unknown_column(name, "item")
        return raw_results

    if not any(str(s) in id_map for s in body.seed_items):
        raise _NoUsableSeeds(list(body.seed_items))

    if body.user_features is not None:
        # Case B: the same solve the pre-existing path runs, plus the
        # profile prior.
        try:
            raw_results, unknown_columns = (
                entry.recommender.get_recommendation_for_new_user(
                    body.seed_items,
                    cutoff=body.limit,
                    user_features=body.user_features,
                )
            )
        except ColdStartNumericalError as exc:
            raise _ColdStartValueUnusable(str(exc)) from None
        except ValueError as exc:
            raise _ColdStartUnsupported(str(exc)) from None
        _metrics.inc_cold_start_request(name, "features_and_history")
        for column in unknown_columns:
            _metrics.inc_feature_unknown_value(name, "user", column)
        if _has_undeclared_columns(
            entry.recommender.user_feature_state, body.user_features
        ):
            _metrics.inc_feature_unknown_column(name, "user")
        return raw_results

    # All seeds known, no user_features: byte-for-byte the pre-existing
    # path -- unchanged so existing clients see no behavior change.
    try:
        raw_results = entry.recommender.get_recommendation_for_new_user(
            body.seed_items, body.limit
        )
    except KeyError:
        # Unexpected KeyError despite seed appearing known.
        logger.exception(
            "recommender_unexpected_key_error",
            recipe=name,
            verb=verb,
            seed_items_count=len(body.seed_items),
        )
        raise

    if not raw_results:
        raise _NoCandidates()
    return raw_results


def make_router(
    registry: ModelRegistry,
    api_keys: list[ApiKeyEntry],
    insecure_no_auth: bool = False,
) -> APIRouter:
    router = APIRouter()

    # S5: distinguish explicit --insecure-no-auth from "no keys configured".
    _bypass_mode = "insecure_no_auth" if insecure_no_auth else "loopback_no_keys"

    def _require_auth(request: Request) -> str:
        return verify_api_key(request, api_keys, bypass_mode=_bypass_mode)

    def _resolve_entry(
        name: str, request_id: str, kid: str, status_holder: list[str]
    ) -> ModelEntry:
        entry = registry.get(name)
        if entry is None:
            status_holder[0] = "recipe_not_found"
            logger.warning(
                "recipe_not_found",
                name=name,
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
                "recipe_not_loaded",
                name=name,
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
        recipe_name: str = "",
        verb: str = "",
    ) -> tuple[list[RecommendItem], int, int]:
        """Build the item list for a recommend response.

        Returns ``(items, fallback_count, dropped_count)``.  The caller is
        responsible for setting ``X-Recotem-Items-Degraded`` and incrementing
        the degraded-items metrics when either count is non-zero.
        """
        items: list[RecommendItem] = []
        fallback_count = 0
        dropped_count = 0
        for item_id, score in raw_results:
            if item_id in exclude:
                continue
            fields: dict[str, Any] = {}
            if meta_index is not None:
                fields.update(meta_index.get(item_id, {}))
            fields["item_id"] = item_id
            fields["score"] = float(score)
            try:
                items.append(RecommendItem.model_validate(fields))
            except ValidationError as exc:
                logger.warning(
                    "metadata_serialization_failed",
                    item_id=str(item_id),
                    error=str(exc)[:200],
                    recipe=recipe_name,
                )
                if recipe_name:
                    _metrics.inc_metadata_serialization_error(recipe_name, verb)
                # Fallback: serve item with only item_id and score.
                bare: dict[str, Any] = {"item_id": item_id, "score": float(score)}
                try:
                    items.append(RecommendItem.model_validate(bare))
                    fallback_count += 1
                except ValidationError:
                    # Even bare item fails (e.g. invalid item_id) — drop it.
                    dropped_count += 1
        return items, fallback_count, dropped_count

    def _apply_build_items_degraded(
        items_result: tuple[list[RecommendItem], int, int],
        response: Response,
        recipe_name: str,
        verb: str,
    ) -> list[RecommendItem]:
        """Apply degraded-item side-effects and return the item list."""
        items, fallback_count, dropped_count = items_result
        degraded = fallback_count + dropped_count
        if degraded > 0:
            response.headers["X-Recotem-Items-Degraded"] = str(degraded)
        if fallback_count > 0:
            _metrics.inc_metadata_degraded_items(
                recipe_name, verb, "fallback", fallback_count
            )
        if dropped_count > 0:
            _metrics.inc_metadata_degraded_items(
                recipe_name, verb, "dropped", dropped_count
            )
        return items

    @router.get("/health", summary="Overall health status (probe-safe)")
    def health(response: Response) -> dict[str, Any]:
        # Intentional design difference vs /health/details: this probe endpoint
        # uses count-based degraded detection (loaded < total) under a single
        # lock acquisition so the two numbers are consistent with each other.
        # /health/details performs a per-recipe error scan for richer operator
        # diagnostics; see health_details below.
        loaded_count, total = registry.health_counts()
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
        # Intentional design difference vs /health: this operator endpoint
        # checks per-recipe error fields (any last_load_error → degraded) and
        # exposes per-recipe details for diagnostics.  /health uses the cheaper
        # count-based check so it is safe for high-frequency liveness probes.
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
        name: str = Path(pattern=_RECIPE_NAME_RE),
        body: RecommendRequest = ...,
        request: Request = ...,
        response: Response = ...,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "recommend"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid, status_holder)

                try:
                    raw_results = _resolve_recommend(entry, name, verb, body)
                except _NoUsableUser:
                    status_holder[0] = "unknown_user"
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "detail": "user not seen during training",
                            "code": "UNKNOWN_USER",
                        },
                    ) from None
                except _ColdStartUnsupported as exc:
                    status_holder[0] = "features_not_supported"
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "detail": str(exc),
                            "code": "FEATURES_NOT_SUPPORTED",
                        },
                    ) from None
                except _ColdStartValueUnusable as exc:
                    status_holder[0] = "feature_value_unusable"
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "detail": (
                                "one or more supplied feature values "
                                "produced a standardized value that is "
                                "numerically unusable for this model's "
                                f"cold-start scoring: {exc}"
                            ),
                            "code": "FEATURE_VALUE_UNUSABLE",
                        },
                    ) from None
                except KeyError:
                    # Unexpected recommender layout (already logged inside
                    # _resolve_recommend).
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "detail": "internal error",
                            "code": "INTERNAL_ERROR",
                        },
                    ) from None

                exclude = (
                    frozenset(body.exclude_items) if body.exclude_items else frozenset()
                )
                items = _apply_build_items_degraded(
                    _build_items(
                        raw_results, exclude, entry.metadata_index, name, verb
                    ),
                    response,
                    name,
                    verb,
                )

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
            except Exception:
                raise

    @router.post(
        "/recipes/{name}:recommend-related",
        response_model=RecommendResponse,
        summary="Recommend items related to a seed list",
    )
    def recommend_related(
        name: str = Path(pattern=_RECIPE_NAME_RE),
        body: RecommendRelatedRequest = ...,
        request: Request = ...,
        response: Response = ...,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "recommend-related"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid, status_holder)

                try:
                    raw_results = _resolve_recommend_related(entry, name, verb, body)
                except _NoUsableSeeds:
                    status_holder[0] = "unknown_seed_items"
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "detail": "no known seed_items",
                            "code": "UNKNOWN_SEED_ITEMS",
                        },
                    ) from None
                except _ColdStartUnsupported as exc:
                    status_holder[0] = "features_not_supported"
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "detail": str(exc),
                            "code": "FEATURES_NOT_SUPPORTED",
                        },
                    ) from None
                except _ColdStartValueUnusable as exc:
                    status_holder[0] = "feature_value_unusable"
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "detail": (
                                "one or more supplied feature values "
                                "produced a standardized value that is "
                                "numerically unusable for this model's "
                                f"cold-start scoring: {exc}"
                            ),
                            "code": "FEATURE_VALUE_UNUSABLE",
                        },
                    ) from None
                except _NoCandidates:
                    status_holder[0] = "no_candidates"
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "detail": "no candidates produced by ranker",
                            "code": "NO_CANDIDATES",
                        },
                    ) from None
                except (AttributeError, KeyError):
                    # Unexpected recommender layout (already logged inside
                    # _resolve_recommend_related).
                    status_holder[0] = "error"
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "detail": "internal error",
                            "code": "INTERNAL_ERROR",
                        },
                    ) from None

                exclude = (
                    frozenset(body.exclude_items) if body.exclude_items else frozenset()
                )
                items = _apply_build_items_degraded(
                    _build_items(
                        raw_results, exclude, entry.metadata_index, name, verb
                    ),
                    response,
                    name,
                    verb,
                )

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
            except Exception:
                raise

    @router.post(
        "/recipes/{name}:batch-recommend",
        response_model=BatchRecommendResponse,
        summary="Recommend items for multiple users",
    )
    def batch_recommend(
        name: str = Path(pattern=_RECIPE_NAME_RE),
        body: BatchRecommendRequest = ...,
        request: Request = ...,
        response: Response = ...,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "batch-recommend"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid, status_holder)

                _metrics.observe_batch_size(name, verb, len(body.requests))

                results: list[BatchResultOk | BatchResultErr] = []
                aggregate_limit = 0
                for idx, raw in enumerate(body.requests):
                    with _bind_batch_idx(idx):
                        if not isinstance(raw, dict):
                            results.append(
                                _batch_error_entry(
                                    idx,
                                    "VALIDATION_ERROR",
                                    "request must be an object",
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "VALIDATION_ERROR"
                            )
                            continue
                        try:
                            single = RecommendRequest.model_validate(raw)
                        except ValidationError as exc:
                            _msg = _format_batch_validation_message(exc)
                            logger.warning(
                                "batch_element_validation_failed",
                                recipe=name,
                                verb=verb,
                                idx=idx,
                                errors=_sanitize_validation_errors(exc),
                            )
                            results.append(
                                _batch_error_entry(idx, "VALIDATION_ERROR", _msg)
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "VALIDATION_ERROR"
                            )
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
                            _metrics.inc_batch_element_error(
                                name, verb, "VALIDATION_ERROR"
                            )
                            continue
                        aggregate_limit += single.limit
                        try:
                            raw_results = _resolve_recommend(entry, name, verb, single)
                            exclude = (
                                frozenset(single.exclude_items)
                                if single.exclude_items
                                else frozenset()
                            )
                            meta = (
                                entry.metadata_index if body.include_metadata else None
                            )
                            items, _fb, _dr = _build_items(
                                raw_results, exclude, meta, name, verb
                            )
                            if _fb + _dr > 0:
                                if _fb:
                                    _metrics.inc_metadata_degraded_items(
                                        name, verb, "fallback", _fb
                                    )
                                if _dr:
                                    _metrics.inc_metadata_degraded_items(
                                        name, verb, "dropped", _dr
                                    )
                            results.append(
                                BatchResultOk(index=idx, status="ok", items=items)
                            )
                        except _NoUsableUser:
                            results.append(
                                _batch_error_entry(
                                    idx,
                                    "UNKNOWN_USER",
                                    "user not seen during training",
                                )
                            )
                            _metrics.inc_batch_element_error(name, verb, "UNKNOWN_USER")
                        except _ColdStartUnsupported as exc:
                            results.append(
                                _batch_error_entry(
                                    idx, "FEATURES_NOT_SUPPORTED", str(exc)
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "FEATURES_NOT_SUPPORTED"
                            )
                        except _ColdStartValueUnusable as exc:
                            results.append(
                                _batch_error_entry(
                                    idx,
                                    "FEATURE_VALUE_UNUSABLE",
                                    "one or more supplied feature values "
                                    "produced a standardized value that is "
                                    "numerically unusable for this model's "
                                    f"cold-start scoring: {exc}",
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "FEATURE_VALUE_UNUSABLE"
                            )
                        except KeyError:
                            # Unexpected recommender layout (already logged
                            # inside _resolve_recommend): propagate as
                            # INTERNAL_ERROR for observability.
                            results.append(
                                _batch_error_entry(
                                    idx, "INTERNAL_ERROR", "internal error"
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "INTERNAL_ERROR"
                            )
                        except (MemoryError, RecursionError):
                            raise
                        except Exception as exc:
                            logger.exception(
                                "batch_element_error",
                                recipe=name,
                                verb=verb,
                                idx=idx,
                                exc_type=type(exc).__name__,
                                exc_module=type(exc).__module__,
                            )
                            results.append(
                                _batch_error_entry(
                                    idx, "INTERNAL_ERROR", "internal error"
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "INTERNAL_ERROR"
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
            except Exception:
                raise

    @router.post(
        "/recipes/{name}:batch-recommend-related",
        response_model=BatchRecommendResponse,
        summary="Recommend items related to multiple seed lists",
    )
    def batch_recommend_related(
        name: str = Path(pattern=_RECIPE_NAME_RE),
        body: BatchRecommendRelatedRequest = ...,
        request: Request = ...,
        response: Response = ...,
        kid: str = Depends(_require_auth),
    ) -> Any:
        request_id = request.state.request_id
        verb = "batch-recommend-related"

        with _request_metrics(name, verb, kid) as status_holder:
            try:
                entry = _resolve_entry(name, request_id, kid, status_holder)

                _metrics.observe_batch_size(name, verb, len(body.requests))

                results: list[BatchResultOk | BatchResultErr] = []
                aggregate_limit = 0
                cold_seed_solves = 0
                for idx, raw in enumerate(body.requests):
                    with _bind_batch_idx(idx):
                        if not isinstance(raw, dict):
                            results.append(
                                _batch_error_entry(
                                    idx,
                                    "VALIDATION_ERROR",
                                    "request must be an object",
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "VALIDATION_ERROR"
                            )
                            continue
                        try:
                            single = RecommendRelatedRequest.model_validate(raw)
                        except ValidationError as exc:
                            _msg = _format_batch_validation_message(exc)
                            logger.warning(
                                "batch_element_validation_failed",
                                recipe=name,
                                verb=verb,
                                idx=idx,
                                errors=_sanitize_validation_errors(exc),
                            )
                            results.append(
                                _batch_error_entry(idx, "VALIDATION_ERROR", _msg)
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "VALIDATION_ERROR"
                            )
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
                            _metrics.inc_batch_element_error(
                                name, verb, "VALIDATION_ERROR"
                            )
                            continue
                        # Checked BEFORE either budget is consumed, so a
                        # rejected element costs neither -- matching the
                        # aggregate-limit branch above.
                        _solves = _cold_seed_solve_bound(single)
                        if cold_seed_solves + _solves > BATCH_COLD_SEED_SOLVE_LIMIT:
                            results.append(
                                _batch_error_entry(
                                    idx,
                                    "VALIDATION_ERROR",
                                    f"aggregate cold-seed cap exceeded: "
                                    f"{BATCH_COLD_SEED_SOLVE_LIMIT}",
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "VALIDATION_ERROR"
                            )
                            continue
                        aggregate_limit += single.limit
                        cold_seed_solves += _solves
                        try:
                            raw_results = _resolve_recommend_related(
                                entry, name, verb, single
                            )
                            exclude = (
                                frozenset(single.exclude_items)
                                if single.exclude_items
                                else frozenset()
                            )
                            meta = (
                                entry.metadata_index if body.include_metadata else None
                            )
                            items, _fb, _dr = _build_items(
                                raw_results, exclude, meta, name, verb
                            )
                            if _fb + _dr > 0:
                                if _fb:
                                    _metrics.inc_metadata_degraded_items(
                                        name, verb, "fallback", _fb
                                    )
                                if _dr:
                                    _metrics.inc_metadata_degraded_items(
                                        name, verb, "dropped", _dr
                                    )
                            results.append(
                                BatchResultOk(index=idx, status="ok", items=items)
                            )
                        except _NoUsableSeeds:
                            results.append(
                                _batch_error_entry(
                                    idx, "UNKNOWN_SEED_ITEMS", "no known seed_items"
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "UNKNOWN_SEED_ITEMS"
                            )
                        except _ColdStartUnsupported as exc:
                            results.append(
                                _batch_error_entry(
                                    idx, "FEATURES_NOT_SUPPORTED", str(exc)
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "FEATURES_NOT_SUPPORTED"
                            )
                        except _ColdStartValueUnusable as exc:
                            results.append(
                                _batch_error_entry(
                                    idx,
                                    "FEATURE_VALUE_UNUSABLE",
                                    "one or more supplied feature values "
                                    "produced a standardized value that is "
                                    "numerically unusable for this model's "
                                    f"cold-start scoring: {exc}",
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "FEATURE_VALUE_UNUSABLE"
                            )
                        except _NoCandidates:
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
                        except (AttributeError, KeyError):
                            # Unexpected recommender layout (already logged
                            # inside _resolve_recommend_related): propagate as
                            # INTERNAL_ERROR for observability.
                            results.append(
                                _batch_error_entry(
                                    idx, "INTERNAL_ERROR", "internal error"
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "INTERNAL_ERROR"
                            )
                        except (MemoryError, RecursionError):
                            raise
                        except Exception as exc:
                            logger.exception(
                                "batch_element_error",
                                recipe=name,
                                verb=verb,
                                idx=idx,
                                exc_type=type(exc).__name__,
                                exc_module=type(exc).__module__,
                            )
                            results.append(
                                _batch_error_entry(
                                    idx, "INTERNAL_ERROR", "internal error"
                                )
                            )
                            _metrics.inc_batch_element_error(
                                name, verb, "INTERNAL_ERROR"
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
            except Exception:
                raise

    @router.get(
        "/recipes",
        response_model=RecipesListResponse,
        summary="List loaded recipes",
    )
    def list_recipes(kid: str = Depends(_require_auth)) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(kid=kid)
        try:
            all_entries = registry.list()
            total = len(all_entries)
            summaries: list[dict[str, Any]] = []
            for e in all_entries:
                if not e.loaded:
                    continue
                summaries.append(
                    {
                        "name": e.name,
                        "model_version": e.model_version if e.artifact_sha256 else None,
                        "loaded_at": e.loaded_at,
                        "supported_verbs": e.supported_verbs,
                        "kind": e.kind,
                    }
                )
            shown = len(summaries)
            if shown < total:
                logger.debug(
                    "recipes_list_filtered",
                    total=total,
                    shown=shown,
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
        name: str = Path(pattern=_RECIPE_NAME_RE),
        request: Request = ...,
        kid: str = Depends(_require_auth),
    ) -> dict[str, Any]:
        request_id = request.state.request_id
        structlog.contextvars.bind_contextvars(kid=kid, recipe=name)
        try:
            e = registry.get(name)
            if e is None:
                logger.warning(
                    "recipe_not_found",
                    name=name,
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
                    "recipe_not_loaded",
                    name=name,
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
                "model_version": e.model_version if e.artifact_sha256 else None,
                "loaded_at": e.loaded_at,
                "supported_verbs": e.supported_verbs,
                "kind": e.kind,
                "config_digest": e.config_digest or None,
                "algorithms": e.algorithms or [],
                "best_algorithm": e.best_class or "",
                "trained_at": hdr.get("trained_at"),
                "best_class": hdr.get("best_class"),
                "best_params": hdr.get("best_params"),
                "best_score": (
                    # Guard against NaN/Inf from old artifacts or buggy trainers.
                    # RecommendItem.score uses allow_inf_nan=False; apply the same
                    # posture here so the response is always valid JSON (M6).
                    _raw_score
                    if (
                        (_raw_score := hdr.get("best_score")) is None
                        or (isinstance(_raw_score, float) and math.isfinite(_raw_score))
                        or not isinstance(_raw_score, float)
                    )
                    else None
                ),
                "metric": hdr.get("metric"),
                "cutoff": hdr.get("cutoff"),
                "tuning": hdr.get("tuning"),
                "data_stats": hdr.get("data_stats"),
                "recotem_version": hdr.get("recotem_version"),
                "irspack_version": hdr.get("irspack_version"),
                "recipe_hash": hdr.get("recipe_hash") or None,
            }
        except HTTPException:
            raise
        except (MemoryError, RecursionError):
            raise
        except Exception:
            raise
        finally:
            structlog.contextvars.unbind_contextvars("kid", "recipe")

    return router


def _batch_error_entry(idx: int, code: ErrorCode, message: str) -> BatchResultErr:
    return BatchResultErr(
        index=idx,
        status="error",
        error=ErrorDetail(code=code, message=message),
    )
