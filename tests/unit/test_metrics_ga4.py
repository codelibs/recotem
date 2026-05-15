from __future__ import annotations

import threading

import pytest


def test_no_op_when_prometheus_missing(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "prometheus_client", None)
    if "recotem._metrics_ga4" in sys.modules:
        del sys.modules["recotem._metrics_ga4"]
    from recotem import _metrics_ga4

    # All increments must be safe when prometheus_client isn't importable.
    _metrics_ga4.inc_ga4_pages("recipe-x")
    _metrics_ga4.inc_ga4_rows("recipe-x", 100)
    _metrics_ga4.set_ga4_quota_remaining("recipe-x", "tokens_per_hour", 1000)


def test_counters_initialize_when_prometheus_available() -> None:
    import sys

    if "recotem._metrics_ga4" in sys.modules:
        del sys.modules["recotem._metrics_ga4"]
    from recotem import _metrics_ga4

    _metrics_ga4.inc_ga4_pages("recipe-y")
    _metrics_ga4.inc_ga4_rows("recipe-y", 42)
    _metrics_ga4.set_ga4_quota_remaining("recipe-y", "tokens_per_hour", 950)
    # No exception is the success condition.


# ---------------------------------------------------------------------------
# C1 — Idempotent initialization across multiple calls
# ---------------------------------------------------------------------------


def test_idempotent_initialization_across_multiple_calls() -> None:
    """Calling the metric functions multiple times must not raise ValueError
    about duplicated timeseries (prometheus_client raises this if Counter or
    Gauge is registered twice with the same name under the same registry)."""
    import sys

    # Reload the module from scratch so _INITIALIZED is False.
    if "recotem._metrics_ga4" in sys.modules:
        del sys.modules["recotem._metrics_ga4"]
    from recotem import _metrics_ga4

    # Call each function twice; idempotent guard must prevent double-register.
    _metrics_ga4.inc_ga4_pages("idempotent-recipe")
    _metrics_ga4.inc_ga4_pages("idempotent-recipe")
    _metrics_ga4.inc_ga4_rows("idempotent-recipe", 10)
    _metrics_ga4.inc_ga4_rows("idempotent-recipe", 10)
    _metrics_ga4.set_ga4_quota_remaining("idempotent-recipe", "tokens_per_hour", 100)
    _metrics_ga4.set_ga4_quota_remaining("idempotent-recipe", "tokens_per_hour", 100)
    # No ValueError or any exception must be raised.


# ---------------------------------------------------------------------------
# T2 — Counter value verified by reading back from registry (CRITICAL-1 + lock)
# ---------------------------------------------------------------------------


def _reset_metrics_module() -> None:
    """Reload _metrics_ga4 with a fresh prometheus_client registry to avoid
    'Duplicated timeseries' errors from prior test runs registering the same
    counter names.
    """
    import sys

    # Remove previously imported modules so they are re-imported fresh.
    for key in list(sys.modules):
        if key in ("recotem._metrics_ga4", "prometheus_client"):
            del sys.modules[key]

    # Re-import prometheus_client with a fresh CollectorRegistry so existing
    # counters don't collide with the ones we are about to create.
    import prometheus_client as pc

    pc.REGISTRY = pc.CollectorRegistry()

    # Also re-import _metrics_ga4 so it picks up the clean registry.
    if "recotem._metrics_ga4" in sys.modules:
        del sys.modules["recotem._metrics_ga4"]


def test_counter_value_incremented_correctly() -> None:
    """inc_ga4_pages and inc_ga4_rows must actually change the counter values."""
    import sys

    # Reload with a fresh registry to avoid collision with metrics registered
    # by other tests that share the same default registry.
    for key in list(sys.modules):
        if key == "recotem._metrics_ga4":
            del sys.modules[key]

    import prometheus_client as pc

    fresh_registry = pc.CollectorRegistry()

    # Patch the Counter/Gauge constructors to use our fresh registry.
    import prometheus_client

    orig_counter = prometheus_client.Counter
    orig_gauge = prometheus_client.Gauge

    def patched_counter(name, doc, labels, registry=fresh_registry, **kw):
        return orig_counter(name, doc, labels, registry=fresh_registry, **kw)

    def patched_gauge(name, doc, labels, registry=fresh_registry, **kw):
        return orig_gauge(name, doc, labels, registry=fresh_registry, **kw)

    prometheus_client.Counter = patched_counter
    prometheus_client.Gauge = patched_gauge

    try:
        if "recotem._metrics_ga4" in sys.modules:
            del sys.modules["recotem._metrics_ga4"]
        from recotem import _metrics_ga4

        recipe = "counter-test-recipe"
        _metrics_ga4.inc_ga4_pages(recipe)
        _metrics_ga4.inc_ga4_pages(recipe)
        _metrics_ga4.inc_ga4_rows(recipe, 50)
        _metrics_ga4.inc_ga4_rows(recipe, 25)

        # Read back values directly from the metric objects.
        pages_value = _metrics_ga4._PAGES.labels(recipe=recipe)._value.get()
        rows_value = _metrics_ga4._ROWS.labels(recipe=recipe)._value.get()

        assert pages_value == pytest.approx(2.0), (
            f"Expected 2 page increments, got {pages_value}"
        )
        assert rows_value == pytest.approx(75.0), (
            f"Expected 75 row increments, got {rows_value}"
        )
    finally:
        prometheus_client.Counter = orig_counter
        prometheus_client.Gauge = orig_gauge


def test_concurrent_init_no_duplicate_timeseries() -> None:
    """Four threads each calling _ensure_initialized() concurrently must not
    raise ValueError: Duplicated timeseries from prometheus_client."""
    import sys

    import prometheus_client

    # Use a fresh isolated registry for this test.
    fresh_registry = prometheus_client.CollectorRegistry()

    orig_counter = prometheus_client.Counter
    orig_gauge = prometheus_client.Gauge

    def patched_counter(name, doc, labels, registry=fresh_registry, **kw):
        return orig_counter(name, doc, labels, registry=fresh_registry, **kw)

    def patched_gauge(name, doc, labels, registry=fresh_registry, **kw):
        return orig_gauge(name, doc, labels, registry=fresh_registry, **kw)

    prometheus_client.Counter = patched_counter
    prometheus_client.Gauge = patched_gauge

    try:
        # Reload to get a fresh _INITIALIZED = False state.
        if "recotem._metrics_ga4" in sys.modules:
            del sys.modules["recotem._metrics_ga4"]
        from recotem import _metrics_ga4

        errors: list[Exception] = []
        barrier = threading.Barrier(4)

        def worker():
            try:
                barrier.wait()  # all threads start together
                _metrics_ga4._ensure_initialized()
                _metrics_ga4.inc_ga4_pages("concurrent-recipe")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent init raised: {errors}"

        # Counter must reflect all 4 increments.
        pages_value = _metrics_ga4._PAGES.labels(
            recipe="concurrent-recipe"
        )._value.get()
        assert pages_value == pytest.approx(4.0), f"Expected 4, got {pages_value}"
    finally:
        prometheus_client.Counter = orig_counter
        prometheus_client.Gauge = orig_gauge
