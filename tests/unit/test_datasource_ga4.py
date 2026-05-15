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
    assert "ga4" in GA4Source.extras_required
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


def test_init_does_not_construct_client_eagerly(monkeypatch) -> None:
    """Client construction is deferred to _get_client(); __init__ must not call it."""
    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    # _client must still be None — no ADC call at init time
    assert src._client is None
    assert fake_mod.BetaAnalyticsDataClient.call_count == 0


def test_init_constructs_client(monkeypatch) -> None:
    """_get_client() constructs the client on first call and caches it."""
    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    client = src._get_client()
    assert client is fake_client
    # Second call must return the same instance without a second construction.
    client2 = src._get_client()
    assert client2 is fake_client
    assert fake_mod.BetaAnalyticsDataClient.call_count == 1


def test_init_client_construction_failure_raises(monkeypatch) -> None:
    """Generic exception from BetaAnalyticsDataClient() is wrapped as DataSourceError."""
    fake_mod = MagicMock()
    fake_mod.BetaAnalyticsDataClient.side_effect = Exception("ADC missing")
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(DataSourceError, match="BetaAnalyticsDataClient"):
        src._get_client()


def test_init_default_credentials_error_raises_clean_message(monkeypatch) -> None:
    """DefaultCredentialsError produces a user-friendly message without ADC paths."""
    from google.auth.exceptions import DefaultCredentialsError

    fake_mod = MagicMock()
    fake_mod.BetaAnalyticsDataClient.side_effect = DefaultCredentialsError(
        "Could not automatically determine credentials from the filesystem or "
        "environment. ADC search path: /home/user/.config/gcloud/..."
    )
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(DataSourceError, match="ADC is not configured") as exc_info:
        src._get_client()
    # The error must NOT leak the ADC search path (chain is suppressed with from None).
    assert exc_info.value.__cause__ is None
    assert "filesystem" not in str(exc_info.value).lower()


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
    call_kwargs = fake_client.run_report.call_args.kwargs
    # probe() must pass timeout= kwarg
    assert "timeout" in call_kwargs
    assert call_kwargs["timeout"] == 60.0
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


def _make_page(rows, row_count=None, property_quota=None, has_data_loss=False):
    """Build a fake GA4 run_report response."""
    m = MagicMock()
    m.rows = rows
    m.row_count = row_count if row_count is not None else len(rows)
    m.property_quota = property_quota
    metadata = MagicMock()
    metadata.data_loss_from_other_row = has_data_loss
    m.metadata = metadata
    return m


def test_fetch_paginates_until_drained(monkeypatch) -> None:
    """Pagination accumulates rows across pages; short final page ends the loop.

    Page 0: 100_000 rows (full page → loop continues).
    Page 1: 4 rows (short page → loop breaks).
    Total: 100_004 rows.
    """
    fake_mod = MagicMock()
    fake_client = MagicMock()

    page_size = 100_000
    full_rows = [
        _row([f"u{i}", "i1", "20260101", "purchase"], "1") for i in range(page_size)
    ]
    partial_rows = [
        _row(["u1", "i1", "20260101", "purchase"], "3"),
        _row(["u2", "i2", "20260102", "purchase"], "1"),
        _row(["u3", "i3", "20260103", "purchase"], "2"),
        _row(["u4", "i4", "20260104", "purchase"], "5"),
    ]
    pages = [
        _make_page(rows=full_rows, row_count=100_004),
        _make_page(rows=partial_rows, row_count=100_004),
    ]
    fake_client.run_report.side_effect = pages
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg(max_rows=1_000_000))
    df = src.fetch(_fetch_ctx())
    assert len(df) == 100_004
    assert list(df.columns) == ["userId", "itemId", "date", "event_count"]
    assert df["event_count"].dtype.name == "int64"
    # Two API calls: one full page + one short page
    assert fake_client.run_report.call_count == 2


def test_fetch_drops_event_name_column(monkeypatch) -> None:
    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = _make_page(
        rows=[_row(["u1", "i1", "20260101", "purchase"], "1")],
        row_count=1,
    )
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
    resp = _make_page(
        rows=[_row([f"u{i}", "i", "20260101", "purchase"], "1") for i in range(100)],
        row_count=100,
    )
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
    # Return a full page (100_000 rows) every call to never hit a short-page break
    full_page_rows = [_row(["u", "i", "20260101", "purchase"], "1")] * 100_000
    resp = _make_page(rows=full_page_rows, row_count=1_000_000)
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    # Patch the module-level get_ga4_max_pages alias to 3:
    import recotem.datasource.ga4 as ga4_mod

    monkeypatch.setattr(ga4_mod, "get_ga4_max_pages", lambda: 3)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg(max_rows=1_000_000))
    with pytest.raises(DataSourceError, match="max_pages|short"):
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

    resp = _make_page(
        rows=[_row(["u1", "i1", "20260101", "purchase"], "1")],
        row_count=1,
        property_quota=quota_obj,
    )
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
    resp = _make_page(rows=[], row_count=0)
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    df = src.fetch(_fetch_ctx())
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0
    # Empty result still has the right columns.
    assert "userId" in df.columns
    assert "itemId" in df.columns
    assert "date" in df.columns
    assert "event_count" in df.columns


# ---------------------------------------------------------------------------
# T3 — PermissionDenied on page > 0 wraps with proper message format
# ---------------------------------------------------------------------------


def test_fetch_permission_denied_on_page_gt_0_raises(monkeypatch) -> None:
    """PermissionDenied raised on page 1 (not page 0) must still be caught."""
    from google.api_core.exceptions import PermissionDenied

    fake_mod = MagicMock()
    fake_client = MagicMock()

    # Page 0: return a full page so the loop continues to page 1
    full_page_rows = [
        _row([f"u{i}", "i", "20260101", "purchase"], "1") for i in range(100_000)
    ]
    page0 = _make_page(rows=full_page_rows, row_count=2_000_000)
    fake_client.run_report.side_effect = [page0, PermissionDenied("denied on page 1")]
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg(max_rows=50_000_000))
    with pytest.raises(DataSourceError, match="roles/analytics.viewer"):
        src.fetch(_fetch_ctx())


# ---------------------------------------------------------------------------
# Short-page early-break test
# ---------------------------------------------------------------------------


def test_fetch_short_page_breaks_early(monkeypatch) -> None:
    """3 full pages (100k rows each) + 1 partial page = 305k rows, no error."""
    fake_mod = MagicMock()
    fake_client = MagicMock()

    page_size = 100_000
    full_rows = [
        _row([f"u{i}", "i", "20260101", "purchase"], "1") for i in range(page_size)
    ]
    partial_rows = [
        _row([f"u{i}", "i", "20260101", "purchase"], "1") for i in range(5_000)
    ]

    pages = [
        _make_page(rows=full_rows, row_count=305_000),
        _make_page(rows=full_rows, row_count=305_000),
        _make_page(rows=full_rows, row_count=305_000),
        _make_page(rows=partial_rows, row_count=305_000),
    ]
    fake_client.run_report.side_effect = pages
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    import recotem.datasource.ga4 as ga4_mod

    monkeypatch.setattr(ga4_mod, "get_ga4_max_pages", lambda: 500)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg(max_rows=50_000_000))
    df = src.fetch(_fetch_ctx())
    assert len(df) == 305_000
    # Exactly 4 API calls (3 full + 1 partial)
    assert fake_client.run_report.call_count == 4


# ---------------------------------------------------------------------------
# Wall-clock budget exceeded test
# ---------------------------------------------------------------------------


def test_fetch_wall_clock_budget_exceeded(monkeypatch) -> None:
    """Mock time.monotonic advancing past the wall-clock deadline raises DataSourceError."""
    fake_mod = MagicMock()
    fake_client = MagicMock()

    # Return a full page every call so the loop would normally continue
    full_page_rows = [
        _row([f"u{i}", "i", "20260101", "purchase"], "1") for i in range(100_000)
    ]
    resp = _make_page(rows=full_page_rows, row_count=10_000_000)
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    import recotem.datasource.ga4 as ga4_mod

    monkeypatch.setattr(ga4_mod, "get_ga4_max_pages", lambda: 500)

    # Simulate time.monotonic: first call (deadline_wall = t0 + budget) returns 0,
    # subsequent calls (the per-iteration check) return a value past the deadline.
    # api_timeout_seconds=60, budget = 60*10 = 600s
    call_count = [0]

    def fake_monotonic():
        call_count[0] += 1
        if call_count[0] == 1:
            return 0.0  # initial call to set deadline_wall = 600.0
        return 601.0  # immediately past deadline on first page check

    import recotem.datasource.ga4 as ga4_mod2

    monkeypatch.setattr(ga4_mod2.time, "monotonic", fake_monotonic)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg(max_rows=50_000_000))
    with pytest.raises(DataSourceError, match="wall-clock budget"):
        src.fetch(_fetch_ctx())


# ---------------------------------------------------------------------------
# Retry policy deadline = 3× api_timeout test
# ---------------------------------------------------------------------------


def test_retry_policy_deadline_is_3x_api_timeout(monkeypatch) -> None:
    """_retry_policy() must return a Retry whose timeout is 3× api_timeout_seconds."""
    fake_mod = MagicMock()
    fake_mod.BetaAnalyticsDataClient.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg(api_timeout_seconds=60))
    retry = src._retry_policy()
    # google-api-core Retry stores the budget as _timeout (aliased as .timeout and .deadline)
    assert retry._timeout == pytest.approx(180.0)  # 60 * 3


def test_retry_policy_and_timeout_kwarg_passed_to_run_report(monkeypatch) -> None:
    """run_report must be called with both retry= and timeout= kwargs in fetch()."""
    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = _make_page(
        rows=[_row(["u1", "i1", "20260101", "purchase"], "1")], row_count=1
    )
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg(api_timeout_seconds=45))
    src.fetch(_fetch_ctx())

    call_kwargs = fake_client.run_report.call_args.kwargs
    assert "retry" in call_kwargs
    assert "timeout" in call_kwargs
    assert call_kwargs["timeout"] == pytest.approx(45.0)


# ---------------------------------------------------------------------------
# Quota all-missing warning test
# ---------------------------------------------------------------------------


def test_record_quota_all_attrs_missing_emits_warning(monkeypatch) -> None:
    """_record_quota with a quota object that has no recognized attrs emits a warning."""
    import structlog.testing

    fake_mod = MagicMock()
    fake_mod.BetaAnalyticsDataClient.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())

    # quota object where all recognized attrs return None
    quota_obj = MagicMock()
    quota_obj.tokens_per_hour = None
    quota_obj.tokens_per_day = None
    quota_obj.concurrent_requests = None
    quota_obj.server_errors_per_project_per_hour = None

    response = MagicMock()
    response.property_quota = quota_obj

    with structlog.testing.capture_logs() as cap:
        src._record_quota("my-recipe", response)

    event_names = [e["event"] for e in cap]
    assert "ga4_quota_all_attrs_missing" in event_names


# ---------------------------------------------------------------------------
# data_loss_from_other_row warning test
# ---------------------------------------------------------------------------


def test_fetch_data_loss_from_other_row_emits_warning(monkeypatch) -> None:
    """Response with data_loss_from_other_row=True must emit a structlog warning."""
    import structlog.testing

    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = _make_page(
        rows=[_row(["u1", "i1", "20260101", "purchase"], "1")],
        row_count=1,
        has_data_loss=True,
    )
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with structlog.testing.capture_logs() as cap:
        src.fetch(_fetch_ctx())

    event_names = [e["event"] for e in cap]
    assert "ga4_data_loss_from_other_row" in event_names


# ---------------------------------------------------------------------------
# Lazy client construction test
# ---------------------------------------------------------------------------


def test_lazy_client_not_constructed_until_probe_called(monkeypatch) -> None:
    """_client remains None after __init__; probe() triggers construction."""
    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_response = MagicMock(row_count=0, rows=[])
    fake_client.run_report.return_value = fake_response
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    assert src._client is None, "Client must not be constructed in __init__"

    src.probe()
    assert src._client is not None, "Client must be constructed after probe()"


# ---------------------------------------------------------------------------
# Exception message includes underlying exception text (MAJOR-4)
# ---------------------------------------------------------------------------


def test_probe_error_message_includes_exception_text(monkeypatch) -> None:
    """DataSourceError from probe() must include the underlying exception text."""
    from google.api_core.exceptions import InvalidArgument

    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_client.run_report.side_effect = InvalidArgument("dimension X does not exist")
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(DataSourceError, match="dimension X does not exist"):
        src.probe()


def test_fetch_error_message_includes_exception_text(monkeypatch) -> None:
    """DataSourceError from fetch() must include the underlying exception text."""
    from google.api_core.exceptions import InvalidArgument

    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_client.run_report.side_effect = InvalidArgument("metric Y not recognized")
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(DataSourceError, match="metric Y not recognized"):
        src.fetch(_fetch_ctx())


# ---------------------------------------------------------------------------
# C4 — eventCount float→int handling
# ---------------------------------------------------------------------------


def test_fetch_weight_column_integer_float_values_accepted(monkeypatch) -> None:
    """weight_column values like '3.0' and '5.0' must be accepted and cast to int64."""
    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = _make_page(
        rows=[
            _row(["u1", "i1", "20260101", "purchase"], "3.0"),
            _row(["u2", "i2", "20260101", "purchase"], "5.0"),
        ],
        row_count=2,
    )
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    df = src.fetch(_fetch_ctx())
    assert df["event_count"].dtype.name == "int64"
    assert list(df["event_count"]) == [3, 5]


def test_fetch_weight_column_non_integer_float_raises(monkeypatch) -> None:
    """weight_column value '2.9' must raise DataSourceError with the offending value."""
    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = _make_page(
        rows=[
            _row(["u1", "i1", "20260101", "purchase"], "2.9"),
        ],
        row_count=1,
    )
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(DataSourceError, match="2.9"):
        src.fetch(_fetch_ctx())


def test_fetch_weight_column_string_integers_accepted(monkeypatch) -> None:
    """weight_column values like '3' and '5' (string integers) must be cast to int64."""
    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = _make_page(
        rows=[
            _row(["u1", "i1", "20260101", "purchase"], "3"),
            _row(["u2", "i2", "20260101", "purchase"], "5"),
        ],
        row_count=2,
    )
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    df = src.fetch(_fetch_ctx())
    assert df["event_count"].dtype.name == "int64"
    assert list(df["event_count"]) == [3, 5]


# ---------------------------------------------------------------------------
# m3 — except Exception (not BaseException) — KeyboardInterrupt propagates
# ---------------------------------------------------------------------------


def test_get_client_keyboard_interrupt_propagates(monkeypatch) -> None:
    """KeyboardInterrupt from BetaAnalyticsDataClient() must propagate, not be wrapped."""
    fake_mod = MagicMock()
    fake_mod.BetaAnalyticsDataClient.side_effect = KeyboardInterrupt
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(KeyboardInterrupt):
        src._get_client()


# ---------------------------------------------------------------------------
# m6 — GA4 row shape validation
# ---------------------------------------------------------------------------


def _row_bad_dims(n_dims: int, n_metrics: int = 1):
    """Build a fake GA4 row with an unexpected shape."""
    row = MagicMock()
    row.dimension_values = [MagicMock(value=f"v{i}") for i in range(n_dims)]
    row.metric_values = [MagicMock(value="1") for _ in range(n_metrics)]
    return row


def test_fetch_row_too_few_dimensions_raises(monkeypatch) -> None:
    """A row with only 3 dimension_values must raise DataSourceError with page number."""
    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = _make_page(rows=[_row_bad_dims(n_dims=3, n_metrics=1)], row_count=1)
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(DataSourceError, match=r"page 0"):
        src.fetch(_fetch_ctx())


def test_fetch_row_no_metrics_raises(monkeypatch) -> None:
    """A row with 0 metric_values must raise DataSourceError."""
    fake_mod = MagicMock()
    fake_client = MagicMock()
    resp = _make_page(rows=[_row_bad_dims(n_dims=4, n_metrics=0)], row_count=1)
    fake_client.run_report.return_value = resp
    fake_mod.BetaAnalyticsDataClient.return_value = fake_client
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta", fake_mod)
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", MagicMock())

    from recotem.datasource.ga4 import GA4Source

    src = GA4Source(_cfg())
    with pytest.raises(DataSourceError, match=r"unexpected GA4 row shape"):
        src.fetch(_fetch_ctx())


# ---------------------------------------------------------------------------
# m8 — extras_required uses extra name, not PyPI package name
# ---------------------------------------------------------------------------


def test_extras_required_uses_extra_name() -> None:
    """GA4Source.extras_required must list the pyproject.toml extra name 'ga4'."""
    from recotem.datasource.ga4 import GA4Source

    assert GA4Source.extras_required == ["ga4"]
