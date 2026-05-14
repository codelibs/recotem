from __future__ import annotations


def test_no_op_when_prometheus_missing(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "prometheus_client", None)
    if "recotem._metrics_ga4" in sys.modules:
        del sys.modules["recotem._metrics_ga4"]
    from recotem import _metrics_ga4

    # All increments must be safe when prometheus_client isn't importable.
    _metrics_ga4.inc_ga4_pages("recipe-x")
    _metrics_ga4.inc_ga4_rows("recipe-x", 100)
    _metrics_ga4.set_ga4_quota_remaining("recipe-x", "tokensPerHour", 1000)


def test_counters_initialize_when_prometheus_available() -> None:
    import sys

    if "recotem._metrics_ga4" in sys.modules:
        del sys.modules["recotem._metrics_ga4"]
    from recotem import _metrics_ga4

    _metrics_ga4.inc_ga4_pages("recipe-y")
    _metrics_ga4.inc_ga4_rows("recipe-y", 42)
    _metrics_ga4.set_ga4_quota_remaining("recipe-y", "tokensPerHour", 950)
    # No exception is the success condition.
