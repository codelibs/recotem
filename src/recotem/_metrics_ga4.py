"""Prometheus counters and gauges for GA4 Data API fetch activity.

This module is intentionally neutral — it lives at the top-level ``recotem``
package so that the ``datasource/ga4`` module can import it without
depending on ``recotem.serving``, and ``recotem.serving.metrics`` can expose
it via the shared default Prometheus registry.

The metrics are no-ops when ``prometheus_client`` is not installed, following
the same pattern as ``recotem._metrics_bigquery``.

Metric inventory:

| Name                                | Type    | Labels                  |
|-------------------------------------|---------|-------------------------|
| ``recotem_ga4_pages_fetched_total`` | Counter | recipe                  |
| ``recotem_ga4_rows_fetched_total``  | Counter | recipe                  |
| ``recotem_ga4_quota_remaining``     | Gauge   | recipe, quota_type      |
"""

from __future__ import annotations

import threading
from typing import Any, Literal

try:
    from prometheus_client import Counter, Gauge

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via env without extra
    _PROMETHEUS_AVAILABLE = False

# Module-level metric instances.  Initialised lazily on the first call so that
# importing this module does not register metrics in environments where
# prometheus_client is absent.  ``_INITIALIZED`` short-circuits subsequent
# calls so the three metrics are always created atomically as a group, mirroring
# the single early-return guard in ``recotem._metrics_bigquery``.
#
# ``_INIT_LOCK`` ensures that concurrent first-calls from multiple threads
# cannot each attempt to register the same Counter/Gauge name, which would
# raise ``ValueError: Duplicated timeseries`` from prometheus_client.
_INITIALIZED: bool = False
_PAGES: Any = None
_ROWS: Any = None
_QUOTA: Any = None
_INIT_LOCK: threading.Lock = threading.Lock()


def _ensure_initialized() -> None:
    """Idempotently create the counter and gauge objects.

    Thread-safe: all three metrics are created inside ``_INIT_LOCK`` so that
    concurrent callers cannot each register the same timeseries name.

    Partial-failure safety: if registration of any of the three metrics
    raises (e.g. ``ValueError: Duplicated timeseries`` when another module or
    a previous import cycle already registered the same metric name in the
    default registry), the previously-constructed objects are rolled back
    so that subsequent calls do not re-attempt registration of names that
    were already accepted in this run.  We deliberately latch
    ``_INITIALIZED = True`` even on the failure path so the function
    degrades to a stable no-op rather than oscillating between partial
    states.  The failure is recorded as a structured warning.
    """
    global _INITIALIZED, _PAGES, _ROWS, _QUOTA

    if not _PROMETHEUS_AVAILABLE or _INITIALIZED:
        return

    with _INIT_LOCK:
        # Double-checked locking: another thread may have initialised while we
        # were waiting for the lock.
        if _INITIALIZED:
            return

        try:
            pages = Counter(
                "recotem_ga4_pages_fetched_total",
                "GA4 Data API pages fetched during training.",
                ["recipe"],
            )
            rows = Counter(
                "recotem_ga4_rows_fetched_total",
                "GA4 Data API rows fetched during training.",
                ["recipe"],
            )
            quota = Gauge(
                "recotem_ga4_quota_remaining",
                "GA4 Data API propertyQuota remaining at last response.",
                ["recipe", "quota_type"],
            )
        except Exception as exc:
            # Most commonly ``ValueError("Duplicated timeseries...")`` from
            # prometheus_client, but third-party registries can raise other
            # types (KeyError on custom registries, ImportError from a
            # delayed transitive dep, etc.).  Catch broadly so that *any*
            # registration failure latches into a stable no-op rather than
            # leaving ``_INITIALIZED=False`` and retrying on every metric
            # call.  Roll back any partial registration.
            _PAGES = None
            _ROWS = None
            _QUOTA = None
            _INITIALIZED = True
            import structlog as _sl  # local import; logging.py owns config

            _sl.get_logger(__name__).warning(
                "ga4_metrics_init_failed",
                error_class=type(exc).__name__,
                detail=str(exc)[:200],
            )
            return

        _PAGES = pages
        _ROWS = rows
        _QUOTA = quota
        _INITIALIZED = True


# ---------------------------------------------------------------------------
# Literal type for quota_type — fixed cardinality keeps Prometheus label
# cardinality bounded.  Callers MUST restrict to this closed set.
# ---------------------------------------------------------------------------

GA4QuotaType = Literal[
    "tokens_per_hour",
    "tokens_per_day",
    "concurrent_requests",
    "server_errors_per_project_per_hour",
]


def inc_ga4_pages(recipe: str) -> None:
    """Increment the GA4 pages-fetched counter by 1.

    Parameters
    ----------
    recipe:
        The recipe name label (e.g. ``"news_articles"``).
    """
    _ensure_initialized()
    if _PAGES is None:
        return
    _PAGES.labels(recipe=recipe).inc()


def inc_ga4_rows(recipe: str, n: int) -> None:
    """Increment the GA4 rows-fetched counter by *n*.

    Parameters
    ----------
    recipe:
        The recipe name label.
    n:
        Number of rows returned in this page / batch.
    """
    _ensure_initialized()
    if _ROWS is None:
        return
    _ROWS.labels(recipe=recipe).inc(n)


def set_ga4_quota_remaining(
    recipe: str, quota_type: GA4QuotaType, value: float
) -> None:
    """Set the GA4 quota-remaining gauge.

    Parameters
    ----------
    recipe:
        The recipe name label.
    quota_type:
        One of the ``propertyQuota`` token-bucket names returned by the GA4
        Data API.  Must be one of the four snake_case values defined in
        :data:`GA4QuotaType` (``tokens_per_hour``, ``tokens_per_day``,
        ``concurrent_requests``, ``server_errors_per_project_per_hour``) to
        keep Prometheus label cardinality bounded.
    value:
        Remaining token count from the latest API response.
    """
    _ensure_initialized()
    if _QUOTA is None:
        return
    _QUOTA.labels(recipe=recipe, quota_type=quota_type).set(value)
