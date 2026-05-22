"""Prometheus metrics for the Recotem serving layer.

Exposes a small set of recorder functions that are no-ops when
``prometheus_client`` is not installed, so callers never need to guard.

The ``/metrics`` endpoint itself is opt-in via the
``RECOTEM_METRICS_ENABLED`` environment variable (any of ``1``, ``true``,
``yes``, ``on``).  When the variable is unset or any other value, the
endpoint returns 404 even if recorders are populating the registry.

All metrics share the default ``prometheus_client`` registry, which means
``generate_latest()`` exposes both the serving-layer metrics defined here
and the datasource-layer metrics defined in ``recotem._metrics_bigquery``
(e.g. ``recotem_bigquery_storage_fallback_total``).

Metric inventory (matches docs/operations.md):

| Name                                               | Type       | Labels                  |
|----------------------------------------------------|------------|-------------------------|
| ``recotem_v1_requests_total``                      | Counter    | recipe, verb, status    |
| ``recotem_v1_request_latency_seconds``             | Histogram  | recipe, verb            |
| ``recotem_v1_batch_size``                          | Histogram  | recipe, verb            |
| ``recotem_v1_batch_element_errors_total``          | Counter    | recipe, verb, code      |
| ``recotem_v1_metadata_degraded_items_total``       | Counter    | recipe, verb, kind      |
| ``recotem_v1_validation_errors_outside_verb_total``| Counter    | —                       |
| ``recotem_model_loaded``                           | Gauge      | recipe                  |
| ``recotem_artifact_load_failures_total``           | Counter    | recipe, reason          |
| ``recotem_active_recipes``                         | Gauge      | —                       |
| ``recotem_swap_total``                             | Counter    | recipe, result          |
| ``recotem_artifact_stat_failures_total``           | Counter    | recipe                  |
| ``recotem_watcher_unhandled_errors_total``         | Counter    | —                       |
| ``recotem_metadata_index_build_errors_total``      | Counter    | recipe                  |
| ``recotem_metadata_serialization_errors_total``    | Counter    | recipe, verb            |
| ``recotem_recipe_rescan_errors_total``             | Counter    | recipe                  |
| ``recotem_bigquery_storage_fallback_total``        | Counter    | reason                  |
| ``recotem_recipes_dir_scan_failures_total``        | Counter    | error_class             |
| ``recotem_recommender_layout_unexpected_total``    | Counter    | recipe                  |
| ``recotem_watcher_state_divergence_total``         | Counter    | —                       |

Artifact-load reason taxonomy (``recotem_artifact_load_failures_total``):
``read``, ``parse``, ``hmac``, ``header_json``, ``deserialize``, ``metadata``,
``yaml``, ``unexpected``, ``dir_scan``, ``timeout``.
"""

from __future__ import annotations

import os
from typing import Any

from recotem.config import is_truthy_env

try:
    from prometheus_client import Counter, Gauge, Histogram

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via env without extra
    _PROMETHEUS_AVAILABLE = False


_MODEL_LOADED: Any = None
_ARTIFACT_LOAD_FAILURES: Any = None
_ACTIVE_RECIPES: Any = None
_SWAP_TOTAL: Any = None
_ARTIFACT_STAT_FAILURES: Any = None
_WATCHER_UNHANDLED_ERRORS: Any = None
_METADATA_INDEX_BUILD_ERRORS: Any = None
_METADATA_SERIALIZATION_ERRORS: Any = None
_RECIPE_RESCAN_ERRORS: Any = None
_RECOMMENDER_LAYOUT_UNEXPECTED: Any = None
_WATCHER_STATE_DIVERGENCE: Any = None


def metrics_enabled() -> bool:
    """Return True iff ``/metrics`` should be exposed.

    Both conditions must hold: ``prometheus_client`` is importable AND
    ``RECOTEM_METRICS_ENABLED`` is set to a truthy value.  This makes
    metrics a deliberate operator opt-in rather than implicit-on whenever
    the optional dependency happens to be installed.
    """
    return _PROMETHEUS_AVAILABLE and is_truthy_env(
        os.environ.get("RECOTEM_METRICS_ENABLED")
    )


def _ensure_initialized() -> None:
    """Idempotently create the metric objects.

    Called lazily on the first recorder invocation so importing this module
    does not register metrics in environments that disable them.
    """
    global _MODEL_LOADED
    global _ARTIFACT_LOAD_FAILURES, _ACTIVE_RECIPES, _SWAP_TOTAL
    global _ARTIFACT_STAT_FAILURES, _WATCHER_UNHANDLED_ERRORS
    global _METADATA_INDEX_BUILD_ERRORS, _METADATA_SERIALIZATION_ERRORS
    global _RECIPE_RESCAN_ERRORS
    global _RECOMMENDER_LAYOUT_UNEXPECTED, _WATCHER_STATE_DIVERGENCE

    if not _PROMETHEUS_AVAILABLE or _MODEL_LOADED is not None:
        return

    _MODEL_LOADED = Gauge(
        "recotem_model_loaded",
        "1 when the model for a recipe is loaded and serving, 0 otherwise.",
        ["recipe"],
    )
    _ARTIFACT_LOAD_FAILURES = Counter(
        "recotem_artifact_load_failures_total",
        "Total artifact load failures (initial load and watcher reloads). "
        "reason ∈ {read, parse, hmac, header_json, deserialize, metadata, "
        "yaml, unexpected, dir_scan, timeout}.",
        ["recipe", "reason"],
    )
    _ACTIVE_RECIPES = Gauge(
        "recotem_active_recipes",
        "Number of recipes currently loaded and serving.",
    )
    _SWAP_TOTAL = Counter(
        "recotem_swap_total",
        "Total artifact hot-swap attempts, partitioned by result.",
        ["recipe", "result"],
    )
    _ARTIFACT_STAT_FAILURES = Counter(
        "recotem_artifact_stat_failures_total",
        "Total artifact stat() failures (non-FileNotFoundError) that prevented "
        "the watcher from determining whether the artifact has changed.",
        ["recipe"],
    )
    _WATCHER_UNHANDLED_ERRORS = Counter(
        "recotem_watcher_unhandled_errors_total",
        "Total unhandled exceptions in the watcher poll loop. "
        "A high rate here indicates a broken polling environment.",
    )
    _METADATA_INDEX_BUILD_ERRORS = Counter(
        "recotem_metadata_index_build_errors_total",
        "Per-row errors during build_metadata_index at artifact-load time "
        "(load-time; watcher and startup paths).",
        ["recipe"],
    )
    _METADATA_SERIALIZATION_ERRORS = Counter(
        "recotem_metadata_serialization_errors_total",
        "Per-item metadata serialization failures during request-time "
        "response building (request-time; router path).",
        ["recipe", "verb"],
    )
    _RECIPE_RESCAN_ERRORS = Counter(
        "recotem_recipe_rescan_errors_total",
        "Total recipe YAML parse/load errors during watcher directory rescan "
        "(transient failures that leave the existing model serving).",
        ["recipe"],
    )
    _RECOMMENDER_LAYOUT_UNEXPECTED = Counter(
        "recotem_recommender_layout_unexpected_total",
        "Total occurrences of an unexpected recommender internal layout "
        "(AttributeError when accessing _mapper.user_id_to_index or "
        "_mapper.item_id_to_index). "
        "A non-zero rate indicates an irspack API incompatibility.",
        ["recipe"],
    )
    _WATCHER_STATE_DIVERGENCE = Counter(
        "recotem_watcher_state_divergence_total",
        "Total times the watcher attempted to mark a load error on a recipe "
        "that has no registry entry (set_load_error returned False). "
        "Indicates a state ordering bug in the watcher.",
    )


def set_model_loaded(recipe: str, loaded: bool) -> None:
    """Set the per-recipe loaded gauge."""
    _ensure_initialized()
    if _MODEL_LOADED is None:
        return
    _MODEL_LOADED.labels(recipe=recipe).set(1 if loaded else 0)


_LOAD_FAILURE_REASONS: frozenset[str] = frozenset(
    {
        "read",
        "parse",
        "hmac",
        "header_json",
        "deserialize",
        "metadata",
        "yaml",
        "unexpected",
        "dir_scan",
        # Stat hung in the executor thread (object-store non-responsive).
        # Distinct from "read" (file could not be opened/parsed) because stat
        # timeouts are an infrastructure signal rather than a data signal.
        "timeout",
    }
)


def inc_artifact_load_failure(recipe: str, reason: str = "unexpected") -> None:
    """Increment the per-recipe artifact-load-failures counter.

    *reason* must be one of the values in ``_LOAD_FAILURE_REASONS``
    (``read | parse | hmac | header_json | deserialize | metadata | yaml |
    unexpected | dir_scan | timeout``); any other value is silently coerced
    to ``"unexpected"`` so callers cannot accidentally explode the cardinality
    of the label.
    """
    _ensure_initialized()
    if _ARTIFACT_LOAD_FAILURES is None:
        return
    label = reason if reason in _LOAD_FAILURE_REASONS else "unexpected"
    _ARTIFACT_LOAD_FAILURES.labels(recipe=recipe, reason=label).inc()


def set_active_recipes(count: int) -> None:
    """Set the gauge of currently-loaded recipes."""
    _ensure_initialized()
    if _ACTIVE_RECIPES is None:
        return
    _ACTIVE_RECIPES.set(count)


def record_swap(recipe: str, ok: bool) -> None:
    """Record an artifact hot-swap attempt (ok=True → ``ok``, else ``error``)."""
    _ensure_initialized()
    if _SWAP_TOTAL is None:
        return
    _SWAP_TOTAL.labels(recipe=recipe, result="ok" if ok else "error").inc()


def inc_artifact_stat_failure(recipe: str) -> None:
    """Increment the per-recipe artifact-stat-failures counter.

    Called when ``_stat_marker`` encounters an unexpected error (not a plain
    FileNotFoundError) — e.g. S3 throttle, IAM revoke, DNS failure.
    """
    _ensure_initialized()
    if _ARTIFACT_STAT_FAILURES is None:
        return
    _ARTIFACT_STAT_FAILURES.labels(recipe=recipe).inc()


def inc_watcher_unhandled_error() -> None:
    """Increment the global watcher-unhandled-errors counter.

    Called each time the watcher's main poll loop catches an unexpected
    exception.  A sustained high rate indicates a broken environment.
    """
    _ensure_initialized()
    if _WATCHER_UNHANDLED_ERRORS is None:
        return
    _WATCHER_UNHANDLED_ERRORS.inc()


def inc_metadata_index_build_error(recipe: str) -> None:
    """Increment the per-recipe metadata-index-build-errors counter.

    Called at artifact-load time when ``build_metadata_index`` encounters
    a row that cannot be flattened — e.g. ``AttributeError`` from a
    non-unique index returning a DataFrame instead of a Series, or
    ``TypeError`` from a non-string column name.
    """
    _ensure_initialized()
    if _METADATA_INDEX_BUILD_ERRORS is None:
        return
    _METADATA_INDEX_BUILD_ERRORS.labels(recipe=recipe).inc()


def inc_metadata_serialization_error(recipe: str, verb: str) -> None:
    """Increment the per-recipe/verb metadata-serialization-errors counter.

    Called at request time when ``RecommendItem.model_validate`` fails for
    a single item during the metadata-join step in the router.
    """
    _ensure_initialized()
    if _METADATA_SERIALIZATION_ERRORS is None:
        return
    _METADATA_SERIALIZATION_ERRORS.labels(recipe=recipe, verb=verb).inc()


def inc_recipe_rescan_error(recipe: str) -> None:
    """Increment the per-recipe rescan-errors counter.

    Called when the watcher's ``_scan_recipes_dir`` fails to load a YAML for
    a recipe that was previously registered.  The existing model keeps serving;
    this counter surfaces the transient failure in metrics.
    """
    _ensure_initialized()
    if _RECIPE_RESCAN_ERRORS is None:
        return
    _RECIPE_RESCAN_ERRORS.labels(recipe=recipe).inc()


def inc_recommender_layout_unexpected(recipe: str) -> None:
    """Increment the per-recipe recommender-layout-unexpected counter.

    Called when ``_any_seed_known`` encounters an ``AttributeError`` accessing
    ``recommender._mapper.item_id_to_index``, indicating an unexpected irspack
    internal layout.  A non-zero rate signals an API incompatibility.
    """
    _ensure_initialized()
    if _RECOMMENDER_LAYOUT_UNEXPECTED is None:
        return
    _RECOMMENDER_LAYOUT_UNEXPECTED.labels(recipe=recipe).inc()


def inc_watcher_state_divergence() -> None:
    """Increment the watcher-state-divergence counter.

    Called when ``set_load_error_no_entry`` fires in the watcher — i.e. the
    watcher tried to mark a load error on a recipe that has no registry entry.
    Indicates a state ordering bug: the watcher should always insert a stub
    entry before attempting a load.
    """
    _ensure_initialized()
    if _WATCHER_STATE_DIVERGENCE is None:
        return
    _WATCHER_STATE_DIVERGENCE.inc()


# ---------------------------------------------------------------------------
# v1 API metrics
# ---------------------------------------------------------------------------

_V1_REQUEST_COUNTER: Any = None
_V1_REQUEST_LATENCY: Any = None
_V1_BATCH_SIZE: Any = None
_V1_BATCH_ELEMENT_ERRORS: Any = None
_V1_METADATA_DEGRADED_ITEMS: Any = None
_V1_VALIDATION_ERRORS_OUTSIDE_VERB: Any = None


def _ensure_v1_initialized() -> None:
    """Lazily create the v1 counter/histogram families.

    Called from record_v1_request and observe_batch_size.  Mirrors the
    pattern used by _ensure_initialized() for the operational metrics.
    """
    global _V1_REQUEST_COUNTER, _V1_REQUEST_LATENCY, _V1_BATCH_SIZE
    global _V1_BATCH_ELEMENT_ERRORS
    global _V1_METADATA_DEGRADED_ITEMS, _V1_VALIDATION_ERRORS_OUTSIDE_VERB
    if _V1_REQUEST_COUNTER is not None:
        return
    if not metrics_enabled():
        return

    _V1_REQUEST_COUNTER = Counter(
        "recotem_v1_requests_total",
        "Total number of v1 API requests by recipe, verb, and status.",
        ["recipe", "verb", "status"],
    )
    _V1_REQUEST_LATENCY = Histogram(
        "recotem_v1_request_latency_seconds",
        "End-to-end latency of v1 API requests.",
        ["recipe", "verb"],
    )
    _V1_BATCH_SIZE = Histogram(
        "recotem_v1_batch_size",
        "Number of elements in a batch v1 request.",
        ["recipe", "verb"],
        buckets=(1, 2, 4, 8, 16, 32, 64, 128, 256),
    )
    _V1_BATCH_ELEMENT_ERRORS = Counter(
        "recotem_v1_batch_element_errors_total",
        "Per-element errors inside batch v1 responses, partitioned by code. "
        'An outer ``recotem_v1_requests_total{status="ok"}`` increment '
        "still records the HTTP-200 response — this counter surfaces the "
        "per-element failures that the outer counter would otherwise hide.",
        ["recipe", "verb", "code"],
    )
    _V1_METADATA_DEGRADED_ITEMS = Counter(
        "recotem_v1_metadata_degraded_items_total",
        "Items that could not be fully enriched with metadata during response "
        "building. kind=fallback means item_id/score-only fallback was used; "
        "kind=dropped means the item was omitted entirely.",
        ["recipe", "verb", "kind"],
    )
    _V1_VALIDATION_ERRORS_OUTSIDE_VERB = Counter(
        "recotem_v1_validation_errors_outside_verb_total",
        "422 validation errors on non-v1-verb paths (e.g. /v1/recipes listing "
        "with bad query parameters).",
    )


def record_v1_request(
    recipe: str, verb: str, status: str, latency_seconds: float
) -> None:
    """Record a v1 API request.

    *verb* ∈ {"recommend", "recommend-related", "batch-recommend",
    "batch-recommend-related"}.  *status* ∈ {"ok", "unknown_user",
    "unknown_seed_items", "no_candidates", "unavailable",
    "recipe_not_found", "validation_error", "error"}.
    """
    _ensure_v1_initialized()
    if _V1_REQUEST_COUNTER is None:
        return  # metrics disabled
    _V1_REQUEST_COUNTER.labels(recipe=recipe, verb=verb, status=status).inc()
    _V1_REQUEST_LATENCY.labels(recipe=recipe, verb=verb).observe(latency_seconds)


def observe_batch_size(recipe: str, verb: str, size: int) -> None:
    """Record a sample for the batch-size histogram."""
    _ensure_v1_initialized()
    if _V1_BATCH_SIZE is None:
        return
    _V1_BATCH_SIZE.labels(recipe=recipe, verb=verb).observe(size)


def inc_batch_element_error(recipe: str, verb: str, code: str) -> None:
    """Increment the per-element batch-error counter.

    Called once per element that produces ``status="error"`` inside a
    ``:batch-recommend`` or ``:batch-recommend-related`` response so
    operators can alert on per-element failures even though the outer
    HTTP response is still 200.
    """
    _ensure_v1_initialized()
    if _V1_BATCH_ELEMENT_ERRORS is None:
        return
    _V1_BATCH_ELEMENT_ERRORS.labels(recipe=recipe, verb=verb, code=code).inc()


_DEGRADED_ITEM_KINDS: frozenset[str] = frozenset({"fallback", "dropped", "unexpected"})


def inc_metadata_degraded_items(
    recipe: str, verb: str, kind: str, count: int = 1
) -> None:
    """Increment the metadata-degraded-items counter.

    *kind* must be ``"fallback"`` (item served with item_id/score only) or
    ``"dropped"`` (item omitted entirely because even bare-item validation
    failed).  Any value outside ``_DEGRADED_ITEM_KINDS`` is coerced to
    ``"unexpected"`` to prevent accidental label cardinality explosion.
    Called from the router's ``_build_items`` when metadata enrichment degrades
    for one or more items.
    """
    _ensure_v1_initialized()
    if _V1_METADATA_DEGRADED_ITEMS is None:
        return
    label = kind if kind in _DEGRADED_ITEM_KINDS else "unexpected"
    _V1_METADATA_DEGRADED_ITEMS.labels(recipe=recipe, verb=verb, kind=label).inc(count)


def inc_validation_error_outside_verb() -> None:
    """Increment the counter for 422 errors on non-v1-verb paths.

    Called from ``_validation_error_handler`` when the request path does not
    match ``_V1_VERB_PATH_RE`` so operators have a metric for validation
    failures on e.g. ``/v1/recipes`` list endpoints.
    """
    _ensure_v1_initialized()
    if _V1_VALIDATION_ERRORS_OUTSIDE_VERB is None:
        return
    _V1_VALIDATION_ERRORS_OUTSIDE_VERB.inc()


def generate_latest() -> tuple[bytes, str]:
    """Return Prometheus exposition (data, content_type) for the registry.

    Raises RuntimeError if prometheus_client is not importable; callers
    should gate via ``metrics_enabled()`` first.
    """
    if not _PROMETHEUS_AVAILABLE:
        raise RuntimeError("prometheus_client is not installed")
    import prometheus_client  # noqa: PLC0415

    return prometheus_client.generate_latest(), prometheus_client.CONTENT_TYPE_LATEST
