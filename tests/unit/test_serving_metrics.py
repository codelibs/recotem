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
        v1_names = {
            "recotem_v1_requests",
            "recotem_v1_request_latency_seconds",
            "recotem_v1_batch_size",
            "recotem_v1_batch_element_errors",
            "recotem_v1_metadata_degraded_items",
            "recotem_v1_validation_errors_outside_verb",
            "recotem_v1_feature_unknown_value",
            "recotem_v1_feature_unknown_column",
            "recotem_v1_cold_start_requests",
        }
        for collector in list(REGISTRY._names_to_collectors.values()):
            describe = getattr(collector, "describe", None)
            if describe is None:
                continue
            try:
                metrics = describe()
            except Exception:
                continue
            for m in metrics:
                if getattr(m, "name", None) in v1_names:
                    try:
                        REGISTRY.unregister(collector)
                    except (KeyError, ValueError):
                        pass
                    break
        for attr in (
            "_V1_REQUEST_COUNTER",
            "_V1_REQUEST_LATENCY",
            "_V1_BATCH_SIZE",
            "_V1_BATCH_ELEMENT_ERRORS",
            "_V1_METADATA_DEGRADED_ITEMS",
            "_V1_VALIDATION_ERRORS_OUTSIDE_VERB",
            "_V1_FEATURE_UNKNOWN_VALUE",
            "_V1_FEATURE_UNKNOWN_COLUMN",
            "_V1_COLD_START_REQUESTS",
        ):
            setattr(_m, attr, None)

    _teardown()
    yield
    _teardown()


from recotem.serving import metrics as _m  # noqa: E402


def test_record_v1_request_accepts_verb_label(reset_metrics_registry):
    _m.record_v1_request("smartstocknotes", "recommend", "ok", 0.012)
    _m.record_v1_request(
        "smartstocknotes", "recommend-related", "unknown_seed_items", 0.005
    )
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


@pytest.mark.skipif(
    not _prometheus_available(),
    reason="prometheus_client not installed in this environment",
)
def test_inc_metadata_degraded_items_coerces_unknown_kind(reset_metrics_registry):
    """Unknown kind values must be coerced to 'unexpected' to prevent label
    cardinality explosion; known kinds 'fallback' and 'dropped' pass through."""
    _m.inc_metadata_degraded_items("r1", "recommend", "fallback", 2)
    _m.inc_metadata_degraded_items("r1", "recommend", "dropped", 1)
    _m.inc_metadata_degraded_items("r1", "recommend", "arbitrary_future_kind", 3)

    out, _ = _m.generate_latest()
    text = out.decode()

    assert 'kind="fallback"' in text, "fallback kind must appear in output"
    assert 'kind="dropped"' in text, "dropped kind must appear in output"
    assert 'kind="unexpected"' in text, (
        "arbitrary_future_kind must be coerced to 'unexpected'"
    )
    assert "arbitrary_future_kind" not in text, (
        "raw unknown kind must not appear in Prometheus output"
    )


# ---------------------------------------------------------------------------
# Feature-aware iALS cold-start metrics: recotem_v1_feature_unknown_value_total,
# recotem_v1_cold_start_requests_total
# ---------------------------------------------------------------------------


def test_inc_feature_unknown_value_emits_labels(reset_metrics_registry):
    _m.inc_feature_unknown_value("r1", "user", "band")
    _m.inc_feature_unknown_value("r1", "item", "genre", 3)

    out, _ = _m.generate_latest()
    text = out.decode()

    assert "recotem_v1_feature_unknown_value_total" in text
    assert 'side="user"' in text
    assert 'column="band"' in text
    assert 'side="item"' in text
    assert 'column="genre"' in text


def test_inc_feature_unknown_column_emits_labels(reset_metrics_registry):
    _m.inc_feature_unknown_column("r1", "user")
    _m.inc_feature_unknown_column("r1", "item")

    out, _ = _m.generate_latest()
    text = out.decode()

    assert "recotem_v1_feature_unknown_column_total" in text
    assert 'side="user"' in text
    assert 'side="item"' in text


def test_inc_feature_unknown_column_has_no_column_label(reset_metrics_registry):
    """The column name is request input, not recipe content, so it must never
    become a label: an unbounded label value is a metrics-cardinality DoS.
    This is the deliberate asymmetry with ``inc_feature_unknown_value``,
    whose ``column`` label is bounded by the operator's own recipe."""
    _m.inc_feature_unknown_column("r1", "user")

    out, _ = _m.generate_latest()
    line = next(
        ln
        for ln in out.decode().splitlines()
        if ln.startswith("recotem_v1_feature_unknown_column_total{")
    )
    assert "column=" not in line, f"unknown column name must not be a label: {line!r}"
    assert _m.inc_feature_unknown_column.__code__.co_argcount == 2, (
        "signature must stay (recipe, side) so a column name cannot be passed"
    )


def test_inc_feature_unknown_column_coerces_unknown_side(reset_metrics_registry):
    _m.inc_feature_unknown_column("r1", "user")
    _m.inc_feature_unknown_column("r1", "sideways")

    out, _ = _m.generate_latest()
    text = out.decode()

    assert 'side="user"' in text
    assert 'side="unexpected"' in text
    assert 'side="sideways"' not in text


def test_inc_feature_unknown_value_coerces_unknown_side(reset_metrics_registry):
    """Unknown side values must be coerced to 'unexpected' to prevent label
    cardinality explosion; 'item' and 'user' pass through."""
    _m.inc_feature_unknown_value("r1", "user", "band")
    _m.inc_feature_unknown_value("r1", "sideways", "band")

    out, _ = _m.generate_latest()
    text = out.decode()

    assert 'side="user"' in text
    assert 'side="unexpected"' in text
    assert "sideways" not in text, "raw unknown side must not reach Prometheus"


def test_inc_cold_start_request_coerces_unknown_case(reset_metrics_registry):
    _m.inc_cold_start_request("r1", "features_only")
    _m.inc_cold_start_request("r1", "cold_seeds")
    _m.inc_cold_start_request("r1", "arbitrary_future_case")

    out, _ = _m.generate_latest()
    text = out.decode()

    assert 'case="features_only"' in text
    assert 'case="cold_seeds"' in text
    assert 'case="unexpected"' in text
    assert "arbitrary_future_case" not in text


def test_feature_counters_are_noop_when_metrics_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With metrics off the helpers must be inert, not raise."""
    from recotem.serving import metrics as m

    monkeypatch.setattr(m, "metrics_enabled", lambda: False)
    monkeypatch.setattr(m, "_V1_FEATURE_UNKNOWN_VALUE", None)
    monkeypatch.setattr(m, "_V1_COLD_START_REQUESTS", None)
    monkeypatch.setattr(m, "_V1_REQUEST_COUNTER", None)

    m.inc_feature_unknown_value("r1", "user", "band")
    m.inc_cold_start_request("r1", "features_only")

    assert m._V1_FEATURE_UNKNOWN_VALUE is None
    assert m._V1_COLD_START_REQUESTS is None
