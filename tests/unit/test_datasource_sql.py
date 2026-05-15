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
    assert "postgres" in SQLSource.extras_required
    assert "mysql" in SQLSource.extras_required
    assert "sqlite" in SQLSource.extras_required
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
# A3 — _apply_read_only raises DataSourceError on failure (pg/mysql/mariadb)
#       SQLite is a silent no-op.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dialect", ["postgresql", "mysql", "mariadb"])
def test_apply_read_only_raises_on_failure(monkeypatch, dialect) -> None:
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("perm denied")

    orig_dialect = src._dialect
    src._dialect = dialect
    try:
        with pytest.raises(DataSourceError, match="READ ONLY"):
            src._apply_read_only(mock_conn)
    finally:
        src._dialect = orig_dialect


def test_apply_read_only_sqlite_is_noop(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("should not be called")

    # Must not raise — sqlite path returns early before execute()
    src._apply_read_only(mock_conn)
    mock_conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# A4 — _apply_statement_timeout raises DataSourceError on failure (pg/mysql/mariadb)
#       SQLite is a silent no-op.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dialect", ["postgresql", "mysql", "mariadb"])
def test_apply_statement_timeout_raises_on_failure(monkeypatch, dialect) -> None:
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("perm denied")

    orig_dialect = src._dialect
    src._dialect = dialect
    try:
        with pytest.raises(DataSourceError, match="statement_timeout"):
            src._apply_statement_timeout(mock_conn)
    finally:
        src._dialect = orig_dialect


def test_apply_statement_timeout_sqlite_is_noop(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("should not be called")

    # Must not raise — sqlite path returns early before execute()
    src._apply_statement_timeout(mock_conn)
    mock_conn.execute.assert_not_called()


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


# ---------------------------------------------------------------------------
# CRITICAL-1 — IPv6 SSRF: bracket-wrap ensures correct address classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ipv6_addr",
    [
        "::1",  # loopback
        "fe80::1",  # link-local
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
    ],
)
def test_ipv6_private_addresses_blocked(monkeypatch, ipv6_addr) -> None:
    """IPv6 private/loopback addresses in DSN must raise DataSourceError."""
    import types

    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    # psycopg stub so driver probe passes
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"postgresql://u:p@[{ipv6_addr}]/db")
    with pytest.raises(DataSourceError, match="(?i)private|loopback"):
        SQLSource(_make_cfg())


def test_ipv6_public_hostname_not_blocked(monkeypatch) -> None:
    """A public hostname must not raise even without RECOTEM_SQL_ALLOW_PRIVATE."""
    import types
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@db.example.com/orders"
    )
    # Patch assert_host_public to avoid real DNS lookup in CI.
    with patch("recotem.datasource.sql.assert_host_public", return_value="203.0.113.1"):
        # Must not raise DataSourceError for the SSRF check.
        src = SQLSource(_make_cfg())
    assert src._dialect == "postgresql"


# ---------------------------------------------------------------------------
# MAJOR-1 — Row cap overshoot: chunksize capped to min(100_000, cap)
# ---------------------------------------------------------------------------


def _seed_sqlite_n_rows(tmp_path, n: int):
    import sqlite3

    db = tmp_path / "big.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE events (user_id TEXT, item_id TEXT, ts TEXT)")
    con.executemany(
        "INSERT INTO events VALUES (?, ?, ?)",
        [(f"u{i}", f"i{i}", "2026-01-01") for i in range(n)],
    )
    con.commit()
    con.close()
    return db


def test_row_cap_exceeded_raises_before_all_data_loaded(monkeypatch, tmp_path) -> None:
    """With cap=3, seeding 10 rows must raise DataSourceError."""
    import recotem.datasource.sql as sql_mod
    from recotem.datasource.sql import SQLSource

    db = _seed_sqlite_n_rows(tmp_path, 10)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    monkeypatch.setattr(sql_mod, "get_max_sql_rows", lambda: 3)
    src = SQLSource(_make_cfg(query="SELECT user_id, item_id, ts FROM events"))
    with pytest.raises(DataSourceError, match="(?i)exceeds RECOTEM_MAX_SQL_ROWS"):
        src.fetch(_ctx())


def test_row_cap_boundary_exactly_at_cap_succeeds(monkeypatch, tmp_path) -> None:
    """Exactly cap rows must NOT raise."""
    import recotem.datasource.sql as sql_mod
    from recotem.datasource.sql import SQLSource

    db = _seed_sqlite_n_rows(tmp_path, 5)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    monkeypatch.setattr(sql_mod, "get_max_sql_rows", lambda: 5)
    src = SQLSource(_make_cfg(query="SELECT user_id, item_id, ts FROM events"))
    df = src.fetch(_ctx())
    assert len(df) == 5


def test_row_cap_one_over_raises(monkeypatch, tmp_path) -> None:
    """cap+1 rows must raise DataSourceError."""
    import recotem.datasource.sql as sql_mod
    from recotem.datasource.sql import SQLSource

    db = _seed_sqlite_n_rows(tmp_path, 6)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    monkeypatch.setattr(sql_mod, "get_max_sql_rows", lambda: 5)
    src = SQLSource(_make_cfg(query="SELECT user_id, item_id, ts FROM events"))
    with pytest.raises(DataSourceError, match="(?i)exceeds RECOTEM_MAX_SQL_ROWS"):
        src.fetch(_ctx())


# ---------------------------------------------------------------------------
# MAJOR-2 — engine.dispose() called even when exception is raised
# ---------------------------------------------------------------------------


def test_probe_calls_engine_dispose_on_success(monkeypatch) -> None:
    """engine.dispose() must be called after a successful probe()."""
    from unittest.mock import MagicMock, patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__ = lambda s: mock_conn
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("sqlalchemy.create_engine", return_value=mock_engine):
        src.probe()
    mock_engine.dispose.assert_called_once()


def test_probe_calls_engine_dispose_on_exception(monkeypatch) -> None:
    """engine.dispose() must be called even when probe() raises DataSourceError."""
    from unittest.mock import MagicMock, patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("conn refused")
    mock_engine.connect.return_value.__enter__ = lambda s: mock_conn
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("sqlalchemy.create_engine", return_value=mock_engine):
        with pytest.raises(DataSourceError):
            src.probe()
    mock_engine.dispose.assert_called_once()


def test_fetch_calls_engine_dispose_on_exception(monkeypatch) -> None:
    """engine.dispose() must be called even when fetch() raises DataSourceError."""
    from unittest.mock import MagicMock, patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("query blew up")
    mock_engine.connect.return_value.__enter__ = lambda s: mock_conn
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("sqlalchemy.create_engine", return_value=mock_engine):
        with pytest.raises(DataSourceError):
            src.fetch(_ctx())
    mock_engine.dispose.assert_called_once()


# ---------------------------------------------------------------------------
# MINOR-1 — safe_dsn via render_as_string does not leak password
# ---------------------------------------------------------------------------


def test_init_safe_dsn_no_password_with_query_string(monkeypatch) -> None:
    """render_as_string preserves driver+query but hides password."""
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN",
        "postgresql+psycopg://alice:s3cret@db.example.com:5432/orders?sslmode=require",
    )
    with structlog.testing.capture_logs() as captured:
        SQLSource(_make_cfg())
    flat = repr(captured)
    assert "s3cret" not in flat
    assert "alice" not in flat
    # Query string is preserved in the DSN logged
    assert "sslmode" in flat


# ---------------------------------------------------------------------------
# MINOR-2 — dsn_env pattern error message
# ---------------------------------------------------------------------------


def test_dsn_env_bad_name_validation_error(monkeypatch) -> None:
    """A dsn_env value that doesn't match ^RECOTEM_RECIPE_... must raise ValidationError."""
    from pydantic import ValidationError

    from recotem.datasource.sql import SQLConfig

    with pytest.raises(ValidationError):
        SQLConfig(type="sql", dsn_env="DATABASE_URL", query="SELECT 1")

    with pytest.raises(ValidationError):
        SQLConfig(type="sql", dsn_env="MY_DSN", query="SELECT 1")


# ---------------------------------------------------------------------------
# New tests for review findings C1, C2, C3, M3, m2, m5, m7, m8
# ---------------------------------------------------------------------------


# C1 — stream_results execution option is applied
def test_fetch_stream_results_applied(monkeypatch, tmp_path) -> None:
    """engine.connect().execution_options(stream_results=True) must be applied."""
    from unittest.mock import patch

    import recotem.datasource.sql as sql_mod
    from recotem.datasource.sql import SQLSource

    db = _seed_sqlite(tmp_path)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    src = SQLSource(_make_cfg(query="SELECT user_id, item_id, ts FROM events"))

    recorded_options: list[dict] = []

    original_create_engine = sql_mod.pd.read_sql  # keep reference

    import sqlalchemy

    real_create = sqlalchemy.create_engine

    def patched_create_engine(url, **kwargs):
        engine = real_create(url, **kwargs)
        original_connect = engine.connect

        class TrackingConn:
            def __init__(self):
                self._conn = original_connect()

            def execution_options(self, **opts):
                recorded_options.append(dict(opts))
                return self._conn.execution_options(**opts)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self._conn.__exit__(*a)

        original_engine_connect = engine.connect

        def new_connect():
            return TrackingConn()

        engine.connect = new_connect
        return engine

    with patch("sqlalchemy.create_engine", side_effect=patched_create_engine):
        src.fetch(_ctx())

    assert any(opts.get("stream_results") is True for opts in recorded_options), (
        f"stream_results=True was not recorded in execution_options calls: {recorded_options}"
    )


# C2 — MariaDB emits max_statement_time (seconds), not MAX_EXECUTION_TIME
def test_apply_statement_timeout_mariadb_uses_seconds(monkeypatch) -> None:
    """MariaDB must use SET SESSION max_statement_time = <seconds>."""
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg(statement_timeout_seconds=120))
    src._dialect = "mariadb"

    mock_conn = MagicMock()
    src._apply_statement_timeout(mock_conn)

    executed_sqls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
    assert any("max_statement_time" in s for s in executed_sqls), (
        f"expected max_statement_time in executed SQL but got: {executed_sqls}"
    )
    assert not any("MAX_EXECUTION_TIME" in s for s in executed_sqls), (
        f"MAX_EXECUTION_TIME must NOT appear for MariaDB, got: {executed_sqls}"
    )
    # Verify seconds value (not ms)
    assert any("120" in s for s in executed_sqls), (
        f"expected 120 seconds in SQL but got: {executed_sqls}"
    )


# C2 — MySQL emits MAX_EXECUTION_TIME (milliseconds)
def test_apply_statement_timeout_mysql_uses_ms(monkeypatch) -> None:
    """MySQL must use SET SESSION MAX_EXECUTION_TIME = <ms>."""
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg(statement_timeout_seconds=30))
    src._dialect = "mysql"

    mock_conn = MagicMock()
    src._apply_statement_timeout(mock_conn)

    executed_sqls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
    assert any("MAX_EXECUTION_TIME" in s for s in executed_sqls), (
        f"expected MAX_EXECUTION_TIME in executed SQL but got: {executed_sqls}"
    )
    # 30 seconds → 30000 ms
    assert any("30000" in s for s in executed_sqls), (
        f"expected 30000 ms in SQL but got: {executed_sqls}"
    )
    assert not any("max_statement_time" in s for s in executed_sqls), (
        f"max_statement_time must NOT appear for MySQL, got: {executed_sqls}"
    )


# C3 — DNS rebinding detection: different IP on re-check raises DataSourceError
def test_rebinding_different_ip_raises(monkeypatch) -> None:
    """When DNS resolves to a different IP on re-check, DataSourceError is raised."""
    import types
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@db.example.com/orders"
    )

    # During __init__, assert_host_public returns a pinned public IP.
    with patch(
        "recotem.datasource.sql.assert_host_public",
        return_value="203.0.113.1",
    ):
        src = SQLSource(_make_cfg())

    assert src._pinned_ips == {"203.0.113.1"}

    # During _check_rebinding, socket.gethostbyname_ex returns a different IP
    # simulating a DNS rebinding attack.
    def fake_gethostbyname_ex_rebind(host):
        return (host, [], ["10.0.0.1"])

    with patch(
        "recotem.datasource.sql.socket.gethostbyname_ex",
        side_effect=fake_gethostbyname_ex_rebind,
    ):
        with pytest.raises(DataSourceError, match="(?i)rebind"):
            src._check_rebinding()


# C3 — Numeric IP literal skips re-validation
@pytest.mark.parametrize("ip_literal", ["127.0.0.1", "::1"])
def test_rebinding_skipped_for_numeric_ip(monkeypatch, ip_literal) -> None:
    """Numeric IP literals in DSN must skip the DNS re-resolution check."""
    import types
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    encoded = f"[{ip_literal}]" if ":" in ip_literal else ip_literal
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"postgresql://u:p@{encoded}/db")
    src = SQLSource(_make_cfg())

    gethostbyname_called = []

    def fake_gethostbyname_ex(host):
        gethostbyname_called.append(host)
        return (host, [], ["10.0.0.1"])

    with patch("recotem.datasource.sql.socket.gethostbyname_ex", fake_gethostbyname_ex):
        src._check_rebinding()  # must not raise

    assert not gethostbyname_called, (
        "socket.gethostbyname_ex must not be called for numeric IP literals"
    )


# M3 — Unsupported dialect error must not mention "Other SQLAlchemy dialects work"
def test_unsupported_dialect_no_byo_driver_message(monkeypatch) -> None:
    """The error for unsupported dialects must not claim other dialects work."""
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "oracle+cx_oracle://u:p@host/db")
    with pytest.raises(DataSourceError) as exc_info:
        SQLSource(_make_cfg())
    assert "Other SQLAlchemy dialects work" not in str(exc_info.value)


# m2 — safe_dsn fallback only catches AttributeError/TypeError; RuntimeError propagates
def test_safe_dsn_runtimeerror_propagates(monkeypatch) -> None:
    """RuntimeError from URL.create must propagate, not be silently swallowed."""
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@db.example.com/db")
    import types

    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))

    with patch(
        "recotem._http_fetch._resolve_host_addresses",
        return_value=[__import__("ipaddress").ip_address("203.0.113.1")],
    ):
        with patch(
            "sqlalchemy.engine.url.URL.create",
            side_effect=RuntimeError("unexpected internal error"),
        ):
            with pytest.raises(RuntimeError, match="unexpected internal error"):
                SQLSource(_make_cfg())


# m5 — DNS failure message contains "does not resolve", not "private/loopback"
def test_dns_failure_message_does_not_say_private(monkeypatch) -> None:
    """When hostname does not resolve, the error must mention 'does not resolve'."""
    import types
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@nonexistent.invalid/db"
    )

    dns_error_msg = (
        "Refusing fetch to db://nonexistent.invalid: hostname does not resolve. "
        "Set RECOTEM_HTTP_ALLOW_PRIVATE=1 to bypass for offline tests."
    )

    with patch(
        "recotem.datasource.sql.assert_host_public",
        side_effect=__import__(
            "recotem._http_fetch", fromlist=["HttpFetchError"]
        ).HttpFetchError(dns_error_msg),
    ):
        with pytest.raises(DataSourceError) as exc_info:
            SQLSource(_make_cfg())

    msg = str(exc_info.value)
    assert "does not resolve" in msg, f"expected 'does not resolve' in: {msg}"
    assert "private/loopback" not in msg, (
        f"must not say 'private/loopback' for DNS failure: {msg}"
    )


# m7 — make_url raising ValueError is wrapped in DataSourceError
def test_make_url_valueerror_wrapped(monkeypatch) -> None:
    """ValueError from make_url must raise DataSourceError('not a valid SQLAlchemy URL')."""
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "not-a-url")

    with patch(
        "sqlalchemy.engine.url.make_url",
        side_effect=ValueError("bad url"),
    ):
        with pytest.raises(DataSourceError, match="(?i)valid SQLAlchemy URL"):
            SQLSource(_make_cfg())


# m8 — extras_required lists pyproject.toml extra names, not PyPI package names
def test_extras_required_are_extra_names() -> None:
    """extras_required must list pyproject.toml extra names: postgres, mysql, sqlite."""
    from recotem.datasource.sql import SQLSource

    assert SQLSource.extras_required == ["postgres", "mysql", "sqlite"]
