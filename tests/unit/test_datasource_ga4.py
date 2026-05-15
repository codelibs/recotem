from __future__ import annotations

import sys
from datetime import date
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from recotem.datasource.base import DataSourceError, FetchContext


def _cfg(**kw):
    from recotem.datasource.ga4 import GA4Config

    base = dict(
        type="ga4",
        property_id="123456789",
        user_dimension="userId",
        event_names=["purchase"],
        lookback_days=30,
        max_rows=1_000_000,
    )
    base.update(kw)
    return GA4Config(**base)


def test_ga4_config_minimal_valid() -> None:
    c = _cfg()
    assert c.property_id == "123456789"
    assert c.user_dimension == "userId"
    assert c.item_dimension == "itemId"
    assert c.time_dimension == "date"
    assert c.weight_column == "event_count"
    assert c.api_timeout_seconds == 60


def test_ga4_property_id_pattern() -> None:
    with pytest.raises(ValidationError):
        _cfg(property_id="abc")
    with pytest.raises(ValidationError):
        _cfg(property_id="")


def test_ga4_user_dimension_literal() -> None:
    with pytest.raises(ValidationError):
        _cfg(user_dimension="customUserId")


def test_ga4_event_names_pattern() -> None:
    with pytest.raises(ValidationError):
        _cfg(event_names=["1bad"])
    with pytest.raises(ValidationError):
        _cfg(event_names=["bad-name"])
    with pytest.raises(ValidationError):
        _cfg(event_names=["a" * 41])
    _cfg(event_names=["purchase", "view_item", "add_to_cart"])


def test_ga4_event_names_required_nonempty() -> None:
    with pytest.raises(ValidationError):
        _cfg(event_names=[])


def test_ga4_event_names_max_50() -> None:
    with pytest.raises(ValidationError):
        _cfg(event_names=[f"e{i:02d}" for i in range(51)])


def test_ga4_date_range_xor_rolling() -> None:
    with pytest.raises(ValidationError):
        _cfg(lookback_days=30, start_date=date(2026, 1, 1))
    with pytest.raises(ValidationError):
        _cfg(lookback_days=None)
    with pytest.raises(ValidationError):
        _cfg(lookback_days=None, start_date=date(2026, 1, 1))
    _cfg(lookback_days=None, start_date=date(2026, 1, 1), end_date=date(2026, 2, 1))


def test_ga4_date_range_fixed_order() -> None:
    with pytest.raises(ValidationError):
        _cfg(
            lookback_days=None,
            start_date=date(2026, 2, 1),
            end_date=date(2026, 1, 1),
        )


def test_ga4_max_rows_required_and_clamped() -> None:
    with pytest.raises(ValidationError):
        _cfg(max_rows=0)
    with pytest.raises(ValidationError):
        _cfg(max_rows=50_000_001)
    from recotem.datasource.ga4 import GA4Config

    with pytest.raises(ValidationError):
        GA4Config(
            type="ga4",
            property_id="1",
            user_dimension="userId",
            event_names=["x"],
            lookback_days=1,
        )  # max_rows missing


def test_ga4_source_classvars() -> None:
    from recotem.datasource.ga4 import GA4Config, GA4Source

    assert GA4Source.type_name == "ga4"
    assert GA4Source.Config is GA4Config
    assert "google-analytics-data" in GA4Source.extras_required
    assert GA4Source.no_expand_fields == frozenset()


def test_ga4_source_registered_in_registry() -> None:
    from recotem.datasource.registry import get_source_class

    cls = get_source_class("ga4")
    assert cls.__name__ == "GA4Source"


def test_init_missing_extra_raises(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", None)
    from recotem.datasource.ga4 import GA4Source

    with pytest.raises(DataSourceError, match=r"recotem\[ga4\]"):
        GA4Source(_cfg())


def test_init_constructs_client(monkeypatch) -> None:
    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    assert src._client is fake_client


def test_init_client_construction_failure_raises(monkeypatch) -> None:
    fake_mod = MagicMock()
    fake_mod.BetaAnalyticsDataClient.side_effect = Exception("ADC missing")
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)

    from recotem.datasource.ga4 import GA4Source

    with pytest.raises(DataSourceError, match="ADC|GOOGLE_APPLICATION_CREDENTIALS"):
        GA4Source(_cfg())


def test_probe_issues_one_request(monkeypatch) -> None:
    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_response = MagicMock(row_count=0, rows=[])
    fake_client.run_report.return_value = fake_response
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)

    # Also stub the types module to avoid an ImportError in _build_request:
    fake_types = MagicMock()
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", fake_types)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    src.probe()
    assert fake_client.run_report.call_count == 1
    call_args = fake_client.run_report.call_args
    request = call_args.kwargs.get("request") or call_args.args[0]
    # Constructed via fake_types.RunReportRequest(...) — verify it was called once:
    assert fake_types.RunReportRequest.called


def test_probe_permission_denied_raises(monkeypatch) -> None:
    from google.api_core.exceptions import PermissionDenied

    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_client.run_report.side_effect = PermissionDenied("denied")
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(DataSourceError, match="roles/analytics.viewer|property"):
        src.probe()


def _row(values: list[str], metric: str):
    """Build a fake GA4 row protobuf-shape."""
    row = MagicMock()
    row.dimension_values = [MagicMock(value=v) for v in values]
    row.metric_values = [MagicMock(value=metric)]
    return row


def _fetch_ctx() -> FetchContext:
    return FetchContext(recipe_name="t", run_id="r-001")


def test_fetch_paginates_until_drained(monkeypatch) -> None:
    fake_mod = MagicMock()
    fake_client = MagicMock()

    pages = [
        MagicMock(
            row_count=4,
            rows=[
                _row(["u1", "i1", "20260101", "purchase"], "3"),
                _row(["u2", "i2", "20260102", "purchase"], "1"),
            ],
        ),
        MagicMock(
            row_count=4,
            rows=[
                _row(["u3", "i3", "20260103", "purchase"], "2"),
                _row(["u4", "i4", "20260104", "purchase"], "5"),
            ],
        ),
        MagicMock(row_count=4, rows=[]),
    ]
    for p in pages:
        p.property_quota = None
    fake_client.run_report.side_effect = pages
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    df = src.fetch(_fetch_ctx())
    assert len(df) == 4
    assert list(df.columns) == ["userId", "itemId", "date", "event_count"]
    assert df["event_count"].dtype.name == "int64"


def test_fetch_drops_event_name_column(monkeypatch) -> None:
    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = MagicMock(
        row_count=1, rows=[_row(["u1", "i1", "20260101", "purchase"], "1")]
    )
    resp.property_quota = None
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    df = src.fetch(_fetch_ctx())
    assert "eventName" not in df.columns


def test_fetch_max_rows_exceeded(monkeypatch) -> None:
    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = MagicMock(
        row_count=100,
        rows=[_row([f"u{i}", "i", "20260101", "purchase"], "1") for i in range(100)],
    )
    resp.property_quota = None
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg(max_rows=2))
    with pytest.raises(DataSourceError, match="max_rows|exceeds"):
        src.fetch(_fetch_ctx())


def test_fetch_max_pages_exceeded(monkeypatch) -> None:
    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = MagicMock(
        row_count=1_000_000,
        rows=[_row(["u", "i", "20260101", "purchase"], "1")],
    )
    resp.property_quota = None
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    # Patch the module-level get_ga4_max_pages alias to 3:
    import recotem.datasource.ga4 as ga4_mod

    monkeypatch.setattr(ga4_mod, "get_ga4_max_pages", lambda: 3)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg(max_rows=1_000_000))
    with pytest.raises(DataSourceError, match="RECOTEM_GA4_MAX_PAGES|page"):
        src.fetch(_fetch_ctx())


# ---------------------------------------------------------------------------
# B1 — probe wraps non-PermissionDenied GoogleAPICallError
# ---------------------------------------------------------------------------


def test_probe_non_permission_denied_api_error_raises(monkeypatch) -> None:
    from google.api_core.exceptions import InvalidArgument

    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_client.run_report.side_effect = InvalidArgument("bad arg")
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(DataSourceError, match="GA4 probe failed"):
        src.probe()


# ---------------------------------------------------------------------------
# B2 — fetch wraps non-PermissionDenied GoogleAPICallError on first page
# ---------------------------------------------------------------------------


def test_fetch_non_permission_denied_api_error_on_first_page_raises(
    monkeypatch,
) -> None:
    from google.api_core.exceptions import InvalidArgument

    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_client.run_report.side_effect = InvalidArgument("quota exceeded or bad arg")
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(DataSourceError, match="GA4 fetch failed on page 0"):
        src.fetch(_fetch_ctx())


# ---------------------------------------------------------------------------
# B3 — fetch records quota when property_quota carries data
# ---------------------------------------------------------------------------


def test_fetch_records_quota_remaining(monkeypatch) -> None:
    fake_mod = MagicMock()
    fake_client = MagicMock()

    quota_obj = MagicMock()
    quota_obj.tokens_per_hour = MagicMock(remaining=900, consumed=100)
    quota_obj.tokens_per_day = MagicMock(remaining=99000, consumed=1000)
    quota_obj.concurrent_requests = None
    quota_obj.server_errors_per_project_per_hour = None

    resp = MagicMock(
        row_count=1,
        rows=[_row(["u1", "i1", "20260101", "purchase"], "1")],
    )
    resp.property_quota = quota_obj
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    recorder: list[tuple[str, str, float]] = []

    import recotem.datasource.ga4 as ga4_mod

    monkeypatch.setattr(
        ga4_mod,
        "set_ga4_quota_remaining",
        lambda recipe, quota_type, value: recorder.append((recipe, quota_type, value)),
    )

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    src.fetch(_fetch_ctx())

    quota_types_recorded = {r[1] for r in recorder}
    assert "tokens_per_hour" in quota_types_recorded
    assert "tokens_per_day" in quota_types_recorded
    # Values must match the mock remaining counts.
    per_hour = next(r for r in recorder if r[1] == "tokens_per_hour")
    assert per_hour[2] == 900.0
    per_day = next(r for r in recorder if r[1] == "tokens_per_day")
    assert per_day[2] == 99000.0


# ---------------------------------------------------------------------------
# B4 — fetch with zero rows on first page returns empty DataFrame
# ---------------------------------------------------------------------------


def test_fetch_zero_rows_returns_empty_dataframe(monkeypatch) -> None:
    import pandas as pd

    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = MagicMock(row_count=0, rows=[])
    resp.property_quota = None
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    df = src.fetch(_fetch_ctx())
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0
