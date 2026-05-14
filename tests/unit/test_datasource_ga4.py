from __future__ import annotations

import sys
from datetime import date
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from recotem.datasource.base import DataSourceError


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
