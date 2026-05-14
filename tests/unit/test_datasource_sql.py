from __future__ import annotations

import pytest
from pydantic import ValidationError


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
