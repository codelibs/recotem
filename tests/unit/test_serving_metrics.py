"""Unit tests for recotem.serving.metrics and recotem._metrics_bigquery.

Tests:
- recotem_bigquery_storage_fallback_total counter is exposed via /metrics
  when RECOTEM_METRICS_ENABLED=1 and prometheus_client is installed.
- The counter is a no-op when prometheus_client is not available.
- inc_bigquery_storage_fallback increments with the correct label.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prometheus_available() -> bool:
    """Return True iff prometheus_client is importable in this test environment."""
    try:
        import prometheus_client  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Counter increment — unit test against the live counter (if available)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _prometheus_available(),
    reason="prometheus_client not installed in this environment",
)
def test_inc_bigquery_storage_fallback_increments_counter() -> None:
    """inc_bigquery_storage_fallback increments the counter by 1 per call.

    We read the counter value before and after the call to confirm the delta.
    """

    # Reset the module-level counter so the test is idempotent across runs.
    import recotem._metrics_bigquery as mb

    # Force initialisation by calling the function once; then read the counter.
    mb._ensure_initialized()
    assert mb._BIGQUERY_STORAGE_FALLBACK is not None, (
        "_BIGQUERY_STORAGE_FALLBACK must be initialised when prometheus_client "
        "is available"
    )

    before_api = mb._BIGQUERY_STORAGE_FALLBACK.labels(reason="api_error")._value.get()
    before_extra = mb._BIGQUERY_STORAGE_FALLBACK.labels(
        reason="missing_extra"
    )._value.get()

    mb.inc_bigquery_storage_fallback("api_error")
    mb.inc_bigquery_storage_fallback("api_error")
    mb.inc_bigquery_storage_fallback("missing_extra")

    after_api = mb._BIGQUERY_STORAGE_FALLBACK.labels(reason="api_error")._value.get()
    after_extra = mb._BIGQUERY_STORAGE_FALLBACK.labels(
        reason="missing_extra"
    )._value.get()

    assert after_api - before_api == 2.0, (
        f"Expected api_error counter to increase by 2; delta={after_api - before_api}"
    )
    assert after_extra - before_extra == 1.0, (
        f"Expected missing_extra counter to increase by 1; "
        f"delta={after_extra - before_extra}"
    )


# ---------------------------------------------------------------------------
# No-op when prometheus_client is absent
# ---------------------------------------------------------------------------


def test_inc_bigquery_storage_fallback_is_noop_without_prometheus() -> None:
    """When prometheus_client is not importable, calling
    inc_bigquery_storage_fallback must not raise any exception.

    We patch the ``_PROMETHEUS_AVAILABLE`` flag inside a fresh module
    load (without evicting the cached module) to avoid corrupting the
    shared Prometheus default registry for other tests.
    """
    import recotem._metrics_bigquery as mb

    # Temporarily pretend prometheus is unavailable by patching the flag
    # and the counter to None — without evicting the module from sys.modules.
    original_available = mb._PROMETHEUS_AVAILABLE
    original_counter = mb._BIGQUERY_STORAGE_FALLBACK

    try:
        mb._PROMETHEUS_AVAILABLE = False
        mb._BIGQUERY_STORAGE_FALLBACK = None
        # Must not raise even though prometheus is unavailable.
        mb.inc_bigquery_storage_fallback("api_error")
        mb.inc_bigquery_storage_fallback("missing_extra")
    finally:
        mb._PROMETHEUS_AVAILABLE = original_available
        mb._BIGQUERY_STORAGE_FALLBACK = original_counter


# ---------------------------------------------------------------------------
# Exposition via /metrics endpoint
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _prometheus_available(),
    reason="prometheus_client not installed in this environment",
)
def test_bigquery_fallback_counter_exposed_via_metrics_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """With RECOTEM_METRICS_ENABLED=1, the /metrics endpoint must include
    ``recotem_bigquery_storage_fallback_total`` in the Prometheus exposition
    format output.

    The counter lives in ``recotem._metrics_bigquery`` and registers itself
    in the default prometheus_client registry — the same registry that
    ``recotem.serving.metrics.generate_latest()`` queries via
    ``prometheus_client.generate_latest()``.
    """

    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")

    # Trigger counter initialisation via the public API (no-duplicate-safe).
    import recotem._metrics_bigquery as mb

    mb.inc_bigquery_storage_fallback("api_error")

    # generate_latest uses the default prometheus_client registry, which now
    # contains the BQ counter registered by _ensure_initialized().
    import recotem.serving.metrics as srv_metrics

    data, content_type = srv_metrics.generate_latest()
    output = data.decode("utf-8") if isinstance(data, bytes) else data

    assert "recotem_bigquery_storage_fallback_total" in output, (
        "Prometheus /metrics output must include "
        "'recotem_bigquery_storage_fallback_total'; "
        f"got output snippet: {output[:500]!r}"
    )
    assert "text/plain" in content_type, (
        f"Content-Type must be text/plain Prometheus format; got {content_type!r}"
    )


# ---------------------------------------------------------------------------
# v1 API metrics: recotem_v1_requests_total, recotem_v1_request_latency_seconds,
# recotem_v1_batch_size
# ---------------------------------------------------------------------------


@pytest.fixture()
def reset_metrics_registry(monkeypatch: pytest.MonkeyPatch):
    """Reset v1 metric globals and unregister their collectors before/after.

    The prometheus_client default registry is a process-global singleton, so
    re-running a test that creates the same Counter/Histogram name would raise
    "Duplicated timeseries in CollectorRegistry".  This fixture:

    1. Enables metrics via ``RECOTEM_METRICS_ENABLED=1``.
    2. Tears down any pre-existing v1 collectors (defensive — handles state
       leaked from a prior test run within the same process).
    3. Resets the module-level globals so ``_ensure_v1_initialized`` runs.
    4. Repeats teardown after the test so subsequent tests start clean.
    """
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")

    from prometheus_client import REGISTRY

    from recotem.serving import metrics as _m

    def _teardown() -> None:
        for attr in ("_V1_REQUEST_COUNTER", "_V1_REQUEST_LATENCY", "_V1_BATCH_SIZE"):
            collector = getattr(_m, attr, None)
            if collector is not None:
                try:
                    REGISTRY.unregister(collector)
                except (KeyError, ValueError):
                    pass
                setattr(_m, attr, None)

    _teardown()
    yield
    _teardown()


from recotem.serving import metrics as _m  # noqa: E402


def test_record_v1_request_accepts_verb_label(reset_metrics_registry):
    _m.record_v1_request("smartstocknotes", "recommend", "ok", 0.012)
    _m.record_v1_request("smartstocknotes", "recommend-related", "unknown_seed_items", 0.005)
    out, _ = _m.generate_latest()
    text = out.decode()
    assert 'verb="recommend"' in text
    assert 'verb="recommend-related"' in text
    assert 'status="unknown_seed_items"' in text


def test_observe_batch_size_records_histogram(reset_metrics_registry):
    _m.observe_batch_size("smartstocknotes", "batch-recommend", 7)
    out, _ = _m.generate_latest()
    text = out.decode()
    assert "recotem_v1_batch_size_bucket" in text
