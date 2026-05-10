"""Prometheus metrics for the Recotem serving layer.

Exposes a small set of recorder functions that are no-ops when
``prometheus_client`` is not installed, so callers never need to guard.

The ``/metrics`` endpoint itself is opt-in via the
``RECOTEM_METRICS_ENABLED`` environment variable (any of ``1``, ``true``,
``yes``, ``on``).  When the variable is unset or any other value, the
endpoint returns 404 even if recorders are populating the registry.

Metric inventory (matches docs/operations.md):

| Name                                           | Type       | Labels             |
|------------------------------------------------|------------|--------------------|
| ``recotem_predict_total``                      | Counter    | recipe, status     |
| ``recotem_predict_latency_seconds``            | Histogram  | recipe             |
| ``recotem_model_loaded``                       | Gauge      | recipe             |
| ``recotem_artifact_load_failures_total``       | Counter    | recipe             |
| ``recotem_active_recipes``                     | Gauge      | —                  |
| ``recotem_swap_total``                         | Counter    | recipe, result     |
| ``recotem_artifact_stat_failures_total``       | Counter    | recipe             |
| ``recotem_watcher_unhandled_errors_total``     | Counter    | —                  |
"""

from __future__ import annotations

import os
from typing import Any

try:
    from prometheus_client import Counter, Gauge, Histogram

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via env without extra
    _PROMETHEUS_AVAILABLE = False


_PREDICT_TOTAL: Any = None
_PREDICT_LATENCY: Any = None
_MODEL_LOADED: Any = None
_ARTIFACT_LOAD_FAILURES: Any = None
_ACTIVE_RECIPES: Any = None
_SWAP_TOTAL: Any = None
_ARTIFACT_STAT_FAILURES: Any = None
_WATCHER_UNHANDLED_ERRORS: Any = None


def _truthy_env(name: str) -> bool:
    val = os.environ.get(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def metrics_enabled() -> bool:
    """Return True iff ``/metrics`` should be exposed.

    Both conditions must hold: ``prometheus_client`` is importable AND
    ``RECOTEM_METRICS_ENABLED`` is set to a truthy value.  This makes
    metrics a deliberate operator opt-in rather than implicit-on whenever
    the optional dependency happens to be installed.
    """
    return _PROMETHEUS_AVAILABLE and _truthy_env("RECOTEM_METRICS_ENABLED")


def _ensure_initialized() -> None:
    """Idempotently create the metric objects.

    Called lazily on the first recorder invocation so importing this module
    does not register metrics in environments that disable them.
    """
    global _PREDICT_TOTAL, _PREDICT_LATENCY, _MODEL_LOADED
    global _ARTIFACT_LOAD_FAILURES, _ACTIVE_RECIPES, _SWAP_TOTAL
    global _ARTIFACT_STAT_FAILURES, _WATCHER_UNHANDLED_ERRORS

    if not _PROMETHEUS_AVAILABLE or _PREDICT_TOTAL is not None:
        return

    _PREDICT_TOTAL = Counter(
        "recotem_predict_total",
        "Total /predict calls served, partitioned by status.",
        ["recipe", "status"],
    )
    _PREDICT_LATENCY = Histogram(
        "recotem_predict_latency_seconds",
        "End-to-end /predict latency in seconds.",
        ["recipe"],
    )
    _MODEL_LOADED = Gauge(
        "recotem_model_loaded",
        "1 when the model for a recipe is loaded and serving, 0 otherwise.",
        ["recipe"],
    )
    _ARTIFACT_LOAD_FAILURES = Counter(
        "recotem_artifact_load_failures_total",
        "Total artifact load failures (initial load and watcher reloads).",
        ["recipe"],
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


def record_predict(recipe: str, status: str, latency_seconds: float) -> None:
    """Record a /predict call.

    Parameters
    ----------
    recipe:
        Recipe name (the ``{name}`` path parameter from ``/predict/{name}``).
    status:
        One of the following documented status labels:

        - ``"ok"``            — recommendation returned successfully
        - ``"user_not_found"`` — user was not seen during training (HTTP 404)
        - ``"unavailable"``   — recipe not loaded or unhealthy (HTTP 503)
        - ``"error"``         — any other unexpected exception

        Using finer-grained labels avoids alert storms from routine 404s
        (cold-start users) being conflated with genuine 503s or internal errors.
    latency_seconds:
        End-to-end wall-clock time for the request in seconds.
    """
    _ensure_initialized()
    if _PREDICT_TOTAL is None:
        return
    _PREDICT_TOTAL.labels(recipe=recipe, status=status).inc()
    _PREDICT_LATENCY.labels(recipe=recipe).observe(latency_seconds)


def set_model_loaded(recipe: str, loaded: bool) -> None:
    """Set the per-recipe loaded gauge."""
    _ensure_initialized()
    if _MODEL_LOADED is None:
        return
    _MODEL_LOADED.labels(recipe=recipe).set(1 if loaded else 0)


def inc_artifact_load_failure(recipe: str) -> None:
    """Increment the per-recipe artifact-load-failures counter."""
    _ensure_initialized()
    if _ARTIFACT_LOAD_FAILURES is None:
        return
    _ARTIFACT_LOAD_FAILURES.labels(recipe=recipe).inc()


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


def generate_latest() -> tuple[bytes, str]:
    """Return Prometheus exposition (data, content_type) for the registry.

    Raises RuntimeError if prometheus_client is not importable; callers
    should gate via ``metrics_enabled()`` first.
    """
    if not _PROMETHEUS_AVAILABLE:
        raise RuntimeError("prometheus_client is not installed")
    import prometheus_client  # noqa: PLC0415

    return prometheus_client.generate_latest(), prometheus_client.CONTENT_TYPE_LATEST
