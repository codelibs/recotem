from __future__ import annotations

import threading

import pytest


def test_no_op_when_prometheus_missing(monkeypatch) -> None:
    """When prometheus_client is unimportable, all helpers degrade to no-ops.

    Uses ``importlib.reload`` (not ``del sys.modules + import``) because the
    latter does not actually re-execute the module body when the parent
    package still references it as an attribute.
    """
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "prometheus_client", None)

    from recotem import _metrics_ga4

    importlib.reload(_metrics_ga4)

    # All increments must be safe when prometheus_client isn't importable.
    _metrics_ga4.inc_ga4_pages("recipe-x")
    _metrics_ga4.inc_ga4_rows("recipe-x", 100)
    _metrics_ga4.set_ga4_quota_remaining("recipe-x", "tokens_per_hour", 1000)


def test_counters_initialize_when_prometheus_available() -> None:
    import importlib

    from recotem import _metrics_ga4

    importlib.reload(_metrics_ga4)

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
    Gauge is registered twice with the same name under the same registry).

    Uses ``importlib.reload`` so ``_INITIALIZED`` actually starts at False.
    """
    import importlib

    from recotem import _metrics_ga4

    importlib.reload(_metrics_ga4)

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
    """inc_ga4_pages and inc_ga4_rows must actually change the counter values.

    Uses ``importlib.reload`` (NOT the ``del sys.modules + import`` pattern)
    because the latter does not actually re-execute the module body — the
    parent package still references the original module object as an
    attribute, so the import system returns it unchanged.  Reload also
    ensures the module's ``_PROMETHEUS_AVAILABLE`` reflects the current
    test environment, recovering from any earlier test that polluted state.
    """
    import importlib

    import prometheus_client

    fresh_registry = prometheus_client.CollectorRegistry()

    # Patch the Counter/Gauge constructors to use our fresh registry.
    orig_counter = prometheus_client.Counter
    orig_gauge = prometheus_client.Gauge

    def patched_counter(name, doc, labels, registry=fresh_registry, **kw):
        return orig_counter(name, doc, labels, registry=fresh_registry, **kw)

    def patched_gauge(name, doc, labels, registry=fresh_registry, **kw):
        return orig_gauge(name, doc, labels, registry=fresh_registry, **kw)

    prometheus_client.Counter = patched_counter
    prometheus_client.Gauge = patched_gauge

    try:
        from recotem import _metrics_ga4

        importlib.reload(_metrics_ga4)

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
    raise ValueError: Duplicated timeseries from prometheus_client.

    Uses ``importlib.reload`` (see ``test_counter_value_incremented_correctly``
    for rationale) so the module starts with ``_INITIALIZED=False``.
    """
    import importlib

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
        from recotem import _metrics_ga4

        importlib.reload(_metrics_ga4)

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


# ---------------------------------------------------------------------------
# M-1 — partial-init failure leaves the module in a stable no-op state
# ---------------------------------------------------------------------------


def test_partial_init_failure_degrades_to_noop_and_logs(monkeypatch) -> None:
    """If the second Counter raises during init, all three globals stay None.

    Without the rollback, the first Counter would remain registered while
    ``_INITIALIZED`` stayed False — and the *next* call would retry the same
    registration and raise ``Duplicated timeseries`` indefinitely, breaking
    every subsequent GA4 fetch.  The new implementation latches into a
    no-op state on failure.

    Implementation note: ``del sys.modules['recotem._metrics_ga4']`` does
    *not* actually reload the module (the parent package still references
    it as an attribute), so the previous tests' ``Counter`` import remains
    cached.  We patch the module's own ``Counter`` symbol directly and
    reset the state flags so the next ``_ensure_initialized`` call runs
    against our fake.
    """
    import prometheus_client
    import structlog.testing

    from recotem import _metrics_ga4 as _mg

    # Capture the real Counter (the test fixture may run after test 1, which
    # leaves ``_PROMETHEUS_AVAILABLE = False`` and may not have imported
    # ``Counter`` into the module namespace at all — so we go directly to
    # prometheus_client).
    real_counter = prometheus_client.Counter

    call_count = {"n": 0}

    class _FakeCounter:
        def __init__(self, name, doc, labels, **kw):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise ValueError(
                    f"Duplicated timeseries in CollectorRegistry: {{'{name}'}}"
                )
            # First call: register into an isolated registry so it is genuinely
            # usable, demonstrating the rollback isn't accidental.
            self._real = real_counter(
                name,
                doc,
                labels,
                registry=prometheus_client.CollectorRegistry(),
            )

        def labels(self, **kw):
            return self._real.labels(**kw)

    # Inject Counter into the module namespace whether or not the previous
    # tests left it there.
    monkeypatch.setattr(_mg, "Counter", _FakeCounter, raising=False)
    # Force a fresh init path even if a previous test already initialised.
    monkeypatch.setattr(_mg, "_INITIALIZED", False)
    monkeypatch.setattr(_mg, "_PAGES", None)
    monkeypatch.setattr(_mg, "_ROWS", None)
    monkeypatch.setattr(_mg, "_QUOTA", None)
    monkeypatch.setattr(_mg, "_PROMETHEUS_AVAILABLE", True)

    with structlog.testing.capture_logs() as logs:
        _mg.inc_ga4_pages("any")  # triggers _ensure_initialized()

    assert _mg._INITIALIZED is True, "should latch to True even on failure"
    assert _mg._PAGES is None, "first Counter must be rolled back to None"
    assert _mg._ROWS is None
    assert _mg._QUOTA is None

    events = [r for r in logs if r["event"] == "ga4_metrics_init_failed"]
    assert events, f"expected ga4_metrics_init_failed warning, got {logs!r}"
    assert events[0]["error_class"] == "ValueError"

    # Subsequent calls must remain no-ops; specifically they must NOT raise
    # Duplicated timeseries by trying to register again.
    _mg.inc_ga4_rows("any", 10)  # no exception
    _mg.set_ga4_quota_remaining("any", "tokens_per_hour", 0)  # no exception
