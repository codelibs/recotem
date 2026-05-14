"""Unit tests for the echo-source DataSource plugin.

Tests:
- probe() returns expected dict with documented keys when config is valid
- probe() raises DataSourceError when n_rows > n_users * n_items
- fetch() returns a DataFrame with the correct columns and shape
- plugin satisfies the DataSource protocol contract
"""

from __future__ import annotations

import pytest

from recotem.datasource.base import DataSourceError


def _make_source(
    n_users: int = 10, n_items: int = 20, n_rows: int = 50, seed: int = 42
):
    """Construct an EchoSource with the given parameters."""
    from recotem_echo.source import EchoSource

    config = EchoSource.Config(
        n_users=n_users, n_items=n_items, n_rows=n_rows, seed=seed
    )
    return EchoSource(config)


# ---------------------------------------------------------------------------
# probe() happy path
# ---------------------------------------------------------------------------


def test_probe_returns_dict_with_documented_keys() -> None:
    """probe() must return a dict containing 'status', 'rows_to_emit', 'items'."""
    source = _make_source(n_users=5, n_items=10, n_rows=20)
    result = source.probe()
    assert isinstance(result, dict), f"probe() must return a dict, got {type(result)}"
    assert "status" in result, "probe() dict must include 'status'"
    assert "rows_to_emit" in result, "probe() dict must include 'rows_to_emit'"
    assert "items" in result, "probe() dict must include 'items'"


def test_probe_status_is_ok_when_config_is_valid() -> None:
    """probe() must return {'status': 'ok', ...} for a valid config."""
    source = _make_source(n_users=5, n_items=10, n_rows=20)
    result = source.probe()
    assert result["status"] == "ok"


def test_probe_rows_to_emit_matches_config() -> None:
    """probe() must report rows_to_emit == config.n_rows."""
    source = _make_source(n_users=5, n_items=10, n_rows=30)
    result = source.probe()
    assert result["rows_to_emit"] == 30


def test_probe_items_matches_config() -> None:
    """probe() must report items == config.n_items."""
    source = _make_source(n_users=5, n_items=15, n_rows=20)
    result = source.probe()
    assert result["items"] == 15


# ---------------------------------------------------------------------------
# probe() negative path — contradictory config
# ---------------------------------------------------------------------------


def test_probe_raises_DataSourceError_when_n_rows_exceeds_max_possible() -> None:
    """probe() must raise DataSourceError when n_rows > n_users * n_items.

    This is a self-contradictory config: it is impossible to sample n_rows
    distinct user-item pairs from the Cartesian product.
    """
    # 3 users * 2 items = 6 max; request 10 rows
    source = _make_source(n_users=3, n_items=2, n_rows=10)
    with pytest.raises(DataSourceError, match="n_rows"):
        source.probe()


def test_probe_does_not_raise_at_exact_max_possible() -> None:
    """probe() must NOT raise when n_rows == n_users * n_items (boundary)."""
    source = _make_source(n_users=3, n_items=4, n_rows=12)  # exactly 3*4
    result = source.probe()
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# fetch() integration baseline
# ---------------------------------------------------------------------------


def test_fetch_returns_dataframe_with_correct_columns() -> None:
    """fetch() must return a DataFrame with user_id, item_id, timestamp columns."""
    source = _make_source(n_users=5, n_items=10, n_rows=20)
    df = source.fetch(ctx=object())  # ctx is not used by EchoSource
    assert list(df.columns) == ["user_id", "item_id", "timestamp"]


def test_fetch_returns_correct_row_count() -> None:
    """fetch() must return exactly n_rows rows."""
    source = _make_source(n_users=5, n_items=10, n_rows=20)
    df = source.fetch(ctx=object())
    assert len(df) == 20


def test_fetch_raises_DataSourceError_when_n_rows_exceeds_max_possible() -> None:
    """fetch() must raise DataSourceError when n_rows > n_users * n_items."""
    source = _make_source(n_users=2, n_items=2, n_rows=10)
    with pytest.raises(DataSourceError):
        source.fetch(ctx=object())


# ---------------------------------------------------------------------------
# plugin contract compliance
# ---------------------------------------------------------------------------


def test_echo_source_satisfies_datasource_protocol_contract() -> None:
    """EchoSource must pass validate_plugin_contract without raising."""
    from recotem_echo.source import EchoSource

    from recotem.datasource.base import validate_plugin_contract

    validate_plugin_contract(EchoSource)  # must not raise


def test_echo_source_type_name_is_echo() -> None:
    """type_name must be 'echo' to match the recipe YAML discriminator."""
    from recotem_echo.source import EchoSource

    assert EchoSource.type_name == "echo"


def test_echo_source_probe_signature_is_callable() -> None:
    """probe must be a callable attribute on EchoSource instances."""
    source = _make_source()
    assert callable(getattr(source, "probe", None)), (
        "EchoSource must expose a callable probe() method"
    )
