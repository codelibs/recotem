"""Prometheus counter for BigQuery Storage Read API fallback events.

This module is intentionally neutral — it lives at the top-level ``recotem``
package so that the ``datasource/bigquery`` module can import it without
depending on ``recotem.serving``.

.. warning::

   **This counter is not scrapeable.**  It is only ever incremented by the
   BigQuery data source, which runs in ``recotem train`` — a batch process
   with no HTTP server.  ``/v1/metrics`` is served by ``recotem serve``, which
   never fetches data, so the series a scrape returns from a serving process
   is always absent (or zero when something else in that process happens to
   register it).  It is kept as in-process state for a future train-side
   exporter; do not build alerts on it.

   The operable signal for a Storage Read API fallback is the structured log
   event ``bigquery_storage_fallback`` emitted by
   ``recotem.datasource.bigquery``.  See ``docs/operations.md``.

The counter is a no-op when ``prometheus_client`` is not installed, following
the same pattern as ``recotem.serving.metrics``.

Counter inventory:

| Name                                        | Type    | Labels |
|---------------------------------------------|---------|--------|
| ``recotem_bigquery_storage_fallback_total`` | Counter | reason |
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via env without extra
    _PROMETHEUS_AVAILABLE = False

# Module-level counter instance.  Initialised lazily on the first call to
# ``inc_bigquery_storage_fallback`` so that importing this module does not
# register metrics in environments where prometheus_client is absent.
_BIGQUERY_STORAGE_FALLBACK: Any = None


def _ensure_initialized() -> None:
    """Idempotently create the counter object."""
    global _BIGQUERY_STORAGE_FALLBACK

    if not _PROMETHEUS_AVAILABLE or _BIGQUERY_STORAGE_FALLBACK is not None:
        return

    _BIGQUERY_STORAGE_FALLBACK = Counter(
        "recotem_bigquery_storage_fallback_total",
        "Total BigQuery Storage Read API fallback events. "
        "A sustained rate indicates a missing IAM permission "
        "(bigquery.readSessions.create) or a missing optional dependency "
        "(google-cloud-bigquery-storage).",
        ["reason"],
    )


def inc_bigquery_storage_fallback(reason: str) -> None:
    """Increment the BigQuery storage-fallback counter.

    Parameters
    ----------
    reason:
        One of the following documented reason labels:

        - ``"missing_extra"``            — google-cloud-bigquery-storage is not
                                           installed, so the download ran over
                                           REST
        - ``"api_error"``                — IAM-shaped GoogleAPICallError from
                                           the Storage Read API (PermissionDenied
                                           / Forbidden / 403 when the service
                                           account lacks
                                           bigquery.readSessions.create); the
                                           REST fallback ran
        - ``"non_iam_error_no_fallback"`` — non-IAM GoogleAPICallError from the
                                           Storage Read API (quota, 5xx,
                                           connectivity); no fallback, the run
                                           fails.  This label fires only for
                                           genuine download failures: query
                                           execution errors are separated out
                                           before the download starts and never
                                           reach it.

        Using a short, stable label keeps cardinality bounded and makes
        PromQL queries predictable (e.g.
        ``recotem_bigquery_storage_fallback_total{reason="api_error"}``).
    """
    _ensure_initialized()
    if _BIGQUERY_STORAGE_FALLBACK is None:
        return
    _BIGQUERY_STORAGE_FALLBACK.labels(reason=reason).inc()
