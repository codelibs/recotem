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

from typing import Any

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
_INITIALIZED: bool = False
_PAGES: Any = None
_ROWS: Any = None
_QUOTA: Any = None


def _ensure_initialized() -> None:
    """Idempotently create the counter and gauge objects."""
    global _INITIALIZED, _PAGES, _ROWS, _QUOTA

    if not _PROMETHEUS_AVAILABLE or _INITIALIZED:
        return

    _PAGES = Counter(
        "recotem_ga4_pages_fetched_total",
        "GA4 Data API pages fetched during training.",
        ["recipe"],
    )
    _ROWS = Counter(
        "recotem_ga4_rows_fetched_total",
        "GA4 Data API rows fetched during training.",
        ["recipe"],
    )
    _QUOTA = Gauge(
        "recotem_ga4_quota_remaining",
        "GA4 Data API propertyQuota remaining at last response.",
        ["recipe", "quota_type"],
    )
    _INITIALIZED = True


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


def set_ga4_quota_remaining(recipe: str, quota_type: str, value: float) -> None:
    """Set the GA4 quota-remaining gauge.

    Parameters
    ----------
    recipe:
        The recipe name label.
    quota_type:
        One of the ``propertyQuota`` token-bucket names returned by the GA4
        Data API, e.g. ``"tokensPerHour"`` or ``"tokensPerDay"``.  Callers
        MUST restrict this value to the closed set of GA4 ``propertyQuota``
        field names (``tokens_per_hour``, ``tokens_per_day``,
        ``concurrent_requests``, ``server_errors_per_project_per_hour``,
        and the analogous ``tokensPer*`` camelCase variants) to keep
        Prometheus label cardinality bounded.
    value:
        Remaining token count from the latest API response.
    """
    _ensure_initialized()
    if _QUOTA is None:
        return
    _QUOTA.labels(recipe=recipe, quota_type=quota_type).set(value)
