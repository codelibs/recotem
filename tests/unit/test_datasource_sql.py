from __future__ import annotations

import sys

import pytest
import structlog
from pydantic import ValidationError

from recotem.datasource.base import DataSourceError, FetchContext


def test_sql_config_minimal_valid() -> None:
    from recotem.datasource.sql import SQLConfig

    cfg = SQLConfig(
        type="sql",
        dsn_env="RECOTEM_RECIPE_DB_DSN",
        query="SELECT 1",
    )
    assert cfg.type == "sql"
    assert cfg.dsn_env == "RECOTEM_RECIPE_DB_DSN"
    assert cfg.query == "SELECT 1"
    assert cfg.query_parameters == {}
    assert cfg.connect_timeout_seconds == 10
    assert cfg.statement_timeout_seconds == 300


def test_sql_config_rejects_bad_dsn_env_name() -> None:
    from recotem.datasource.sql import SQLConfig

    with pytest.raises(ValidationError):
        SQLConfig(type="sql", dsn_env="MY_DB_DSN", query="SELECT 1")
    with pytest.raises(ValidationError):
        SQLConfig(type="sql", dsn_env="recotem_recipe_db_dsn", query="SELECT 1")
    with pytest.raises(ValidationError):
        SQLConfig(type="sql", dsn_env="RECOTEM_RECIPE_*", query="SELECT 1")


def test_sql_config_rejects_empty_query() -> None:
    from recotem.datasource.sql import SQLConfig

    with pytest.raises(ValidationError):
        SQLConfig(type="sql", dsn_env="RECOTEM_RECIPE_DB_DSN", query="")


def test_sql_config_clamps_timeouts() -> None:
    from recotem.datasource.sql import SQLConfig

    with pytest.raises(ValidationError):
        SQLConfig(
            type="sql",
            dsn_env="RECOTEM_RECIPE_DB_DSN",
            query="SELECT 1",
            connect_timeout_seconds=0,
        )
    with pytest.raises(ValidationError):
        SQLConfig(
            type="sql",
            dsn_env="RECOTEM_RECIPE_DB_DSN",
            query="SELECT 1",
            statement_timeout_seconds=10_000,
        )


def test_sql_config_extra_forbid() -> None:
    from recotem.datasource.sql import SQLConfig

    with pytest.raises(ValidationError):
        SQLConfig(
            type="sql",
            dsn_env="RECOTEM_RECIPE_DB_DSN",
            query="SELECT 1",
            unknown_field="x",
        )


def test_sql_source_classvars() -> None:
    from recotem.datasource.sql import SQLConfig, SQLSource

    assert SQLSource.type_name == "sql"
    assert SQLSource.Config is SQLConfig
    assert "sqlalchemy" in SQLSource.extras_required
    assert SQLSource.no_expand_fields == frozenset({"query", "dsn_env"})


def test_sql_source_registered_in_registry() -> None:
    from recotem.datasource.registry import get_source_class

    cls = get_source_class("sql")
    assert cls.__name__ == "SQLSource"


# ---------------------------------------------------------------------------
# Task 2.4 — SQLSource.__init__ validation tests
# ---------------------------------------------------------------------------


def _make_cfg(**kw):
    from recotem.datasource.sql import SQLConfig

    base = dict(
        type="sql",
        dsn_env="RECOTEM_RECIPE_DB_DSN",
        query="SELECT user_id, item_id, ts FROM events",
    )
    base.update(kw)
    return SQLConfig(**base)


def test_init_missing_dsn_env_raises(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_RECIPE_DB_DSN", raising=False)
    with pytest.raises(DataSourceError, match="RECOTEM_RECIPE_DB_DSN"):
        SQLSource(_make_cfg())


def test_init_unknown_dialect_raises(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "weirddb://host/db")
    with pytest.raises(DataSourceError, match="dialect|driver"):
        SQLSource(_make_cfg())


def test_init_missing_driver_extra_pg(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@db.example.com/x")
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(DataSourceError, match=r"recotem\[postgres\]"):
        SQLSource(_make_cfg())


def test_init_rejects_private_host_by_default(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@127.0.0.1/x")
    with pytest.raises(DataSourceError, match="SSRF|private|RECOTEM_SQL_ALLOW_PRIVATE"):
        SQLSource(_make_cfg())


def test_init_allows_private_host_when_opted_in(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@127.0.0.1/x")
    # Driver probe will succeed because psycopg is installed in dev env.
    # SSRF guard must NOT raise because allow_private=1.
    SQLSource(_make_cfg())  # must not raise


def test_init_sqlite_no_host_no_ssrf_check(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    SQLSource(_make_cfg())  # must not raise (no host)


def test_init_does_not_log_dsn_userinfo(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://alice:s3cret@db.public.example/x"
    )
    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    with structlog.testing.capture_logs() as captured:
        SQLSource(_make_cfg())
    flat = repr(captured)
    assert "alice" not in flat
    assert "s3cret" not in flat


def test_init_log_safe_dsn_format(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN",
        "postgresql://alice:s3cret@db.public.example:5432/orders",
    )
    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    with structlog.testing.capture_logs() as captured:
        SQLSource(_make_cfg())
    flat = repr(captured)
    # safe DSN includes scheme, host, port, and DB path separator — but no creds
    assert "postgresql://" in flat
    assert "db.public.example" in flat
    assert "/orders" in flat  # path separator preserved
    assert "alice" not in flat
    assert "s3cret" not in flat


def test_probe_sqlite_in_memory(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    src.probe()  # no exception


def test_probe_unreachable_db_raises(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@127.0.0.1:1/none")
    src = SQLSource(_make_cfg(connect_timeout_seconds=1))
    with pytest.raises(DataSourceError):
        src.probe()


# ---------------------------------------------------------------------------
# Task 2.6 — SQLSource.fetch tests
# ---------------------------------------------------------------------------


def _ctx() -> FetchContext:
    return FetchContext(recipe_name="t", run_id="r-001")


def _seed_sqlite(tmp_path):
    import sqlite3

    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE events (user_id TEXT, item_id TEXT, ts TEXT);
        INSERT INTO events VALUES ('u1','i1','2026-01-01');
        INSERT INTO events VALUES ('u2','i2','2026-01-02');
        INSERT INTO events VALUES ('u1','i2','2026-01-03');
        """
    )
    con.commit()
    con.close()
    return db


def test_fetch_sqlite_happy_path(monkeypatch, tmp_path) -> None:
    from recotem.datasource.sql import SQLSource

    db = _seed_sqlite(tmp_path)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    src = SQLSource(
        _make_cfg(query="SELECT user_id, item_id, ts FROM events ORDER BY ts")
    )
    df = src.fetch(_ctx())
    assert list(df.columns) == ["user_id", "item_id", "ts"]
    assert len(df) == 3


def test_fetch_parameters_bind_safely(monkeypatch, tmp_path) -> None:
    from recotem.datasource.sql import SQLSource

    db = _seed_sqlite(tmp_path)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    src = SQLSource(
        _make_cfg(
            query="SELECT user_id, item_id, ts FROM events WHERE user_id = :uid",
            query_parameters={"uid": "u1' OR '1'='1"},
        )
    )
    df = src.fetch(_ctx())
    # injection payload is bound as a literal -> matches no rows
    assert len(df) == 0


def test_fetch_max_rows_exceeded(monkeypatch, tmp_path) -> None:
    from recotem.datasource.sql import SQLSource

    db = _seed_sqlite(tmp_path)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    src = SQLSource(_make_cfg(query="SELECT user_id, item_id, ts FROM events"))
    # Patch get_max_sql_rows to return 2 directly (clamp floor is 1000 so we
    # patch the source-module-level alias rather than fight the env clamp).
    import recotem.datasource.sql as sql_mod

    monkeypatch.setattr(sql_mod, "get_max_sql_rows", lambda: 2)
    with pytest.raises(DataSourceError, match="exceeds RECOTEM_MAX_SQL_ROWS|row cap"):
        src.fetch(_ctx())


def test_fetch_passes_columns_through_unchanged(monkeypatch, tmp_path) -> None:
    # Schema diffing happens at the training layer, not in SQLSource.fetch.
    # This test pins that contract: whatever the query produces, fetch returns.
    from recotem.datasource.sql import SQLSource

    db = _seed_sqlite(tmp_path)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    src = SQLSource(_make_cfg(query="SELECT user_id AS u, item_id AS i FROM events"))
    df = src.fetch(_ctx())
    assert list(df.columns) == ["u", "i"]


# ---------------------------------------------------------------------------
# A1 — Malformed DSN raises DataSourceError
# ---------------------------------------------------------------------------


def test_malformed_dsn_raises(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "not-a-url")
    with pytest.raises(DataSourceError, match="(?i)valid SQLAlchemy URL"):
        SQLSource(_make_cfg())


# ---------------------------------------------------------------------------
# A2 — SSRF blocked for RFC1918, link-local, IPv6 ULA
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["10.0.0.1", "192.168.1.5", "172.16.0.1", "169.254.169.254", "fc00::1"],
)
def test_ssrf_blocked_private_hosts(monkeypatch, host) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    encoded = f"[{host}]" if ":" in host else host
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"postgresql://u:p@{encoded}/db")
    monkeypatch.setitem(
        __import__("sys").modules, "psycopg", __import__("sys").modules.get("psycopg")
    )
    with pytest.raises(DataSourceError, match="(?i)private|loopback|SSRF"):
        SQLSource(_make_cfg())


@pytest.mark.parametrize(
    "host",
    ["10.0.0.1", "192.168.1.5", "172.16.0.1", "169.254.169.254", "fc00::1"],
)
def test_ssrf_allow_private_bypasses_ssrf_check(monkeypatch, host) -> None:
    import sys

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    encoded = f"[{host}]" if ":" in host else host
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"postgresql://u:p@{encoded}/db")
    # Stub psycopg so driver probe succeeds even if not installed.
    import types

    fake_psycopg = types.ModuleType("psycopg")
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    # The SSRF DataSourceError specifically must not be raised.
    try:
        SQLSource(_make_cfg())
    except DataSourceError as exc:
        assert (
            "private" not in str(exc).lower()
            and "loopback" not in str(exc).lower()
            and "ssrf" not in str(exc).lower()
        ), f"SSRF error must not appear when allow_private=1, but got: {exc}"


# ---------------------------------------------------------------------------
# A3 — _apply_read_only failure emits warning log
# ---------------------------------------------------------------------------


def test_apply_read_only_failure_emits_warning_log(monkeypatch) -> None:
    from unittest.mock import MagicMock

    import structlog.testing

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("boom")

    with structlog.testing.capture_logs() as captured:
        # Call with postgresql dialect so the execute path is exercised.
        orig_dialect = src._dialect
        src._dialect = "postgresql"
        src._apply_read_only(mock_conn)
        src._dialect = orig_dialect

    events = [e["event"] for e in captured]
    assert "sql_read_only_set_failed" in events


# ---------------------------------------------------------------------------
# A4 — _apply_statement_timeout failure emits warning log
# ---------------------------------------------------------------------------


def test_apply_statement_timeout_failure_emits_warning_log(monkeypatch) -> None:
    from unittest.mock import MagicMock

    import structlog.testing

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("boom")

    with structlog.testing.capture_logs() as captured:
        orig_dialect = src._dialect
        src._dialect = "postgresql"
        src._apply_statement_timeout(mock_conn)
        src._dialect = orig_dialect

    events = [e["event"] for e in captured]
    assert "sql_statement_timeout_set_failed" in events


# ---------------------------------------------------------------------------
# A5 — Whitespace-only DSN raises DataSourceError
# ---------------------------------------------------------------------------


def test_whitespace_only_dsn_raises(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "   ")
    with pytest.raises(DataSourceError, match="not set or is empty"):
        SQLSource(_make_cfg())


# ---------------------------------------------------------------------------
# A6 — fetch with empty result set returns empty DataFrame
# ---------------------------------------------------------------------------


def _seed_sqlite_empty(tmp_path):
    import sqlite3

    db = tmp_path / "empty.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE empty_events (user_id TEXT, item_id TEXT, ts TEXT);"
    )
    con.commit()
    con.close()
    return db


def test_fetch_empty_table_returns_empty_dataframe(monkeypatch, tmp_path) -> None:
    import pandas as pd

    from recotem.datasource.sql import SQLSource

    db = _seed_sqlite_empty(tmp_path)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    src = SQLSource(_make_cfg(query="SELECT user_id, item_id, ts FROM empty_events"))
    df = src.fetch(_ctx())
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# A7 — _connect_args for mysql includes connect_timeout
# ---------------------------------------------------------------------------


def test_connect_args_mysql_has_connect_timeout(monkeypatch) -> None:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg(connect_timeout_seconds=15))
    # Temporarily override dialect to mysql to test branch.
    src._dialect = "mysql"
    args = src._connect_args()
    assert "connect_timeout" in args
    assert args["connect_timeout"] == 15
