from __future__ import annotations

import sys

import pytest
import structlog
from pydantic import ValidationError

from recotem.datasource.base import DataSourceError


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
