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


def test_apply_read_only_sqlite_issues_query_only_pragma(monkeypatch) -> None:
    """SQLite now enforces read-only via PRAGMA query_only=ON, not a no-op.

    The previous silent no-op left users surprised when a Postgres-derived
    recipe (DELETE / UPDATE) ran successfully against SQLite.  The pragma
    rejects writes for the rest of the session.
    """
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()

    src._apply_read_only(mock_conn)

    # Exactly one execute call, carrying the PRAGMA.
    assert mock_conn.execute.call_count == 1, (
        f"expected one PRAGMA execute, got {mock_conn.execute.call_count}"
    )
    pragma_arg = mock_conn.execute.call_args.args[0]
    # SQLAlchemy ``text`` object — render via str() (compiled).
    assert "PRAGMA query_only = ON" in str(pragma_arg), (
        f"expected PRAGMA query_only=ON, got {pragma_arg!r}"
    )


def test_apply_read_only_sqlite_pragma_failure_raises(monkeypatch) -> None:
    """If the PRAGMA execution itself fails, training must fail closed.

    The previous SQLite path was an unconditional no-op; we now treat the
    PRAGMA call as a hard safety control whose failure aborts the run.
    """
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("perm denied")

    with pytest.raises(DataSourceError, match="read-only mode on sqlite"):
        src._apply_read_only(mock_conn)


def test_apply_read_only_sqlite_actually_blocks_writes(monkeypatch, tmp_path) -> None:
    """End-to-end: after PRAGMA query_only=ON, INSERT/UPDATE/DELETE are rejected.

    This complements the unit-level call-count assertions with a real
    SQLite execution to ensure the pragma is wired correctly through
    SQLAlchemy and actually has the documented effect.
    """
    import sqlite3

    import sqlalchemy
    from sqlalchemy import text

    from recotem.datasource.sql import SQLSource

    db = tmp_path / "rw.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE events (user_id TEXT, item_id TEXT, ts TEXT)")
    con.execute("INSERT INTO events VALUES ('u1', 'i1', '2026-01-01')")
    con.commit()
    con.close()

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    src = SQLSource(_make_cfg())

    engine = sqlalchemy.create_engine(str(src._url))
    try:
        with engine.connect() as conn:
            src._apply_read_only(conn)
            # PRAGMA is in effect; INSERT must be rejected at execute time.
            with pytest.raises(sqlalchemy.exc.OperationalError):
                conn.execute(
                    text("INSERT INTO events VALUES ('u2', 'i2', '2026-01-02')")
                )
    finally:
        engine.dispose()


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
        with pytest.raises(DataSourceError, match="statement_timeout") as excinfo:
            src._apply_statement_timeout(mock_conn)
        # Regression for I-3: the driver-supplied message must NOT be folded
        # into the user-facing DataSourceError str(); only the exception class
        # name and the chained __cause__ carry that detail.  This forecloses
        # any future driver from leaking DSN userinfo / hostnames via its own
        # __str__ implementation when SET LOCAL / SET SESSION fails.
        assert "perm denied" not in str(excinfo.value), (
            "DataSourceError must not include the raw driver exception message; "
            f"got {excinfo.value!s}"
        )
    finally:
        src._dialect = orig_dialect


@pytest.mark.parametrize("dialect", ["postgresql", "mysql", "mariadb"])
def test_apply_read_only_does_not_leak_driver_exc_message(monkeypatch, dialect) -> None:
    """I-3 regression — driver exception message stays out of the wrapper.

    psycopg / pymysql operational errors can include DSN userinfo or
    hostnames in their ``__str__``.  The wrapping DataSourceError must
    rely on the class name only.
    """
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    secret = "password authentication failed for user alice"
    mock_conn.execute.side_effect = Exception(secret)

    orig_dialect = src._dialect
    src._dialect = dialect
    try:
        with pytest.raises(DataSourceError) as excinfo:
            src._apply_read_only(mock_conn)
        assert secret not in str(excinfo.value), (
            f"driver-supplied detail leaked into DataSourceError: {excinfo.value!s}"
        )
        # But the chained __cause__ still carries the detail for debugging.
        assert isinstance(excinfo.value.__cause__, Exception)
        assert secret in str(excinfo.value.__cause__)
    finally:
        src._dialect = orig_dialect


def test_apply_statement_timeout_sqlite_warns_and_does_not_execute(monkeypatch) -> None:
    """SQLite still has no statement_timeout; emit a warning instead of a no-op.

    The warning makes operators aware that the documented safety control is
    not in effect on this dialect, rather than letting them assume it is.
    """
    from unittest.mock import MagicMock

    import structlog

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("should not be called")

    with structlog.testing.capture_logs() as logs:
        src._apply_statement_timeout(mock_conn)

    mock_conn.execute.assert_not_called()
    events = [r["event"] for r in logs]
    assert "sql_statement_timeout_unsupported_on_sqlite" in events, (
        f"expected sql_statement_timeout_unsupported_on_sqlite warning, got {events!r}"
    )


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
    # Patch assert_host_public to avoid real DNS lookup in CI.  The function
    # now returns the full list of resolved public IPs (not a single string).
    with patch("recotem.datasource.sql.assert_host_public", return_value=["8.8.8.8"]):
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

    # During __init__, assert_host_public returns the public IP list.  Pin
    # one IPv4 plus one IPv6 to represent a realistic dual-stack hostname.
    with patch(
        "recotem.datasource.sql.assert_host_public",
        return_value=["8.8.8.8", "2606:4700:4700::1111"],
    ):
        src = SQLSource(_make_cfg())

    assert src._pinned_ips == {"8.8.8.8", "2606:4700:4700::1111"}

    # During _check_rebinding, socket.getaddrinfo returns a different IP
    # simulating a DNS rebinding attack.
    import socket

    def fake_getaddrinfo_rebind(host, port, *args, **kwargs):
        # Return only the rebound private IP — no overlap with the pinned set.
        return [(socket.AF_INET, 0, 0, "", ("10.0.0.1", 0))]

    with patch(
        "recotem.datasource.sql.socket.getaddrinfo",
        side_effect=fake_getaddrinfo_rebind,
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

    import socket

    getaddrinfo_called = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        getaddrinfo_called.append(host)
        return [(socket.AF_INET, 0, 0, "", ("10.0.0.1", 0))]

    with patch("recotem.datasource.sql.socket.getaddrinfo", fake_getaddrinfo):
        src._check_rebinding()  # must not raise

    assert not getaddrinfo_called, (
        "socket.getaddrinfo must not be called for numeric IP literals"
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
        return_value=[__import__("ipaddress").ip_address("8.8.8.8")],
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


# ---------------------------------------------------------------------------
# C-1 regression — IPv6 rebinding false-positive
#
# The previous _check_rebinding used socket.gethostbyname_ex which is
# IPv4-only.  On a dual-stack hostname whose first resolved address (and
# therefore the pinned IP) happened to be IPv6, the re-check would never see
# the pinned address and would falsely raise "DNS rebinding detected" on
# every probe/fetch.  These tests assert that the rebinding check now uses
# getaddrinfo, sees IPv4+IPv6 records, and that any single-family overlap
# with the pinned set is sufficient to clear the check.
# ---------------------------------------------------------------------------


def test_rebinding_dual_stack_ipv4_pin_ipv6_rebind_detected(monkeypatch) -> None:
    """Pin = IPv4 only.  Re-resolve returns IPv6 only and IPv4 differs → rebind.

    This is the strict-attack case where DNS legitimately produces two
    families but the IPv4 record changed — we still detect it.
    """
    import socket
    import sys
    import types
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@db.example.com/orders"
    )

    with patch(
        "recotem.datasource.sql.assert_host_public",
        return_value=["8.8.8.8"],
    ):
        src = SQLSource(_make_cfg())

    # Re-resolve returns a different IPv4 plus an IPv6 — no overlap with pin.
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, 0, 0, "", ("1.1.1.1", 0)),
            (socket.AF_INET6, 0, 0, "", ("2606:4700:4700::1001", 0)),
        ]

    with patch("recotem.datasource.sql.socket.getaddrinfo", fake_getaddrinfo):
        with pytest.raises(DataSourceError, match="(?i)rebind"):
            src._check_rebinding()


def test_rebinding_dual_stack_ipv6_pin_ipv4_rebind_overlap_clears(monkeypatch) -> None:
    """Pin = IPv6.  Re-resolve returns IPv4 + IPv6, IPv6 still matches → no raise.

    Regression for C-1: the old gethostbyname_ex (IPv4-only) re-resolver
    would never see the IPv6 entry and would falsely raise.  With
    getaddrinfo, the pinned IPv6 overlaps the current set and the check
    passes.
    """
    import socket
    import sys
    import types
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@db.example.com/orders"
    )

    # __init__ pins both families (the full set returned by assert_host_public).
    with patch(
        "recotem.datasource.sql.assert_host_public",
        return_value=["2606:4700:4700::1111", "8.8.8.8"],
    ):
        src = SQLSource(_make_cfg())

    assert src._pinned_ips == {"2606:4700:4700::1111", "8.8.8.8"}

    # Re-resolve returns the same dual-stack pair (one IP unchanged is enough).
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, 0, 0, "", ("8.8.8.8", 0)),
            (socket.AF_INET6, 0, 0, "", ("2606:4700:4700::1111", 0)),
        ]

    with patch("recotem.datasource.sql.socket.getaddrinfo", fake_getaddrinfo):
        src._check_rebinding()  # MUST NOT raise


def test_rebinding_ipv6_only_pin_ipv6_only_rebind_overlap_clears(monkeypatch) -> None:
    """Pin = IPv6 only, re-resolve returns IPv6 only → check passes.

    Old code with gethostbyname_ex would return an empty IPv4 list and
    incorrectly raise rebinding on every IPv6-only host.
    """
    import socket
    import sys
    import types
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@db6.example.com/orders"
    )

    with patch(
        "recotem.datasource.sql.assert_host_public",
        return_value=["2606:4700:4700::1111"],
    ):
        src = SQLSource(_make_cfg())

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET6, 0, 0, "", ("2606:4700:4700::1111", 0))]

    with patch("recotem.datasource.sql.socket.getaddrinfo", fake_getaddrinfo):
        src._check_rebinding()  # MUST NOT raise


def test_rebinding_oserror_raises_with_clear_message(monkeypatch) -> None:
    """When DNS re-resolution itself fails with OSError, a DataSourceError is raised.

    The error message must indicate aborting to prevent SSRF rather than
    leaking the underlying resolver error verbatim.
    """
    import sys
    import types
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@db.example.com/orders"
    )

    with patch(
        "recotem.datasource.sql.assert_host_public",
        return_value=["8.8.8.8"],
    ):
        src = SQLSource(_make_cfg())

    with patch(
        "recotem.datasource.sql.socket.getaddrinfo",
        side_effect=OSError("name resolution failure"),
    ):
        with pytest.raises(DataSourceError, match="(?i)DNS re-resolution"):
            src._check_rebinding()


# ---------------------------------------------------------------------------
# C-3 — Postgres SET LOCAL / SET TRANSACTION exact-SQL emission
# ---------------------------------------------------------------------------


def test_apply_statement_timeout_postgres_uses_set_local_ms(monkeypatch) -> None:
    """Postgres must issue ``SET LOCAL statement_timeout = <ms>`` (not SET).

    Regression-pin for two distinct bugs:
    * SET LOCAL keeps the timeout scoped to the current transaction; bare
      SET would leak into subsequent transactions on a pooled connection.
    * The Postgres unit is milliseconds (an integer); seconds would silently
      yield a 1000× larger budget.
    """
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg(statement_timeout_seconds=42))
    mock_conn = MagicMock()
    orig_dialect = src._dialect
    src._dialect = "postgresql"
    try:
        src._apply_statement_timeout(mock_conn)
        assert mock_conn.execute.call_count == 1
        emitted_sql = str(mock_conn.execute.call_args.args[0])
        assert "SET LOCAL statement_timeout = 42000" in emitted_sql, (
            f"expected SET LOCAL statement_timeout=42000, got {emitted_sql!r}"
        )
        # Defensive: must NOT be the session-level SET (which would survive
        # past the current transaction on a pooled connection).
        assert "SET LOCAL" in emitted_sql
        assert "SET statement_timeout" not in emitted_sql.replace("SET LOCAL", "")
    finally:
        src._dialect = orig_dialect


def test_apply_read_only_postgres_uses_set_transaction(monkeypatch) -> None:
    """Postgres must issue ``SET TRANSACTION READ ONLY`` exactly once."""
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    orig_dialect = src._dialect
    src._dialect = "postgresql"
    try:
        src._apply_read_only(mock_conn)
        assert mock_conn.execute.call_count == 1
        emitted_sql = str(mock_conn.execute.call_args.args[0])
        assert emitted_sql == "SET TRANSACTION READ ONLY", (
            f"expected exact 'SET TRANSACTION READ ONLY', got {emitted_sql!r}"
        )
    finally:
        src._dialect = orig_dialect


def test_apply_read_only_mysql_uses_set_session_transaction(monkeypatch) -> None:
    """MySQL/MariaDB must issue ``SET SESSION TRANSACTION READ ONLY`` exactly once.

    Distinguishes from the Postgres branch — the SESSION keyword is required
    on MySQL family because the dialect treats unqualified SET TRANSACTION
    as next-transaction-only (the MySQL semantics are not the same as PG).
    """
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    src = SQLSource(_make_cfg())
    mock_conn = MagicMock()
    orig_dialect = src._dialect
    for dialect in ("mysql", "mariadb"):
        mock_conn.reset_mock()
        src._dialect = dialect
        try:
            src._apply_read_only(mock_conn)
            assert mock_conn.execute.call_count == 1
            emitted_sql = str(mock_conn.execute.call_args.args[0])
            assert emitted_sql == "SET SESSION TRANSACTION READ ONLY", (
                f"{dialect}: expected 'SET SESSION TRANSACTION READ ONLY', "
                f"got {emitted_sql!r}"
            )
        finally:
            src._dialect = orig_dialect


# ---------------------------------------------------------------------------
# I-8 — DSN with URL-encoded password special characters
# ---------------------------------------------------------------------------


def test_dsn_password_with_url_encoded_at_sign(monkeypatch) -> None:
    """Password containing an encoded @ (``%40``) must not break DSN parsing.

    SQLAlchemy ``make_url`` accepts percent-encoded credentials; the source
    must round-trip these through ``safe_dsn`` rendering, the SSRF host
    parser, and the log-line emission without crashing or leaking the
    cleartext credential.
    """
    import sys
    import types

    import structlog

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    # Password is ``p@ss:word!`` percent-encoded as ``p%40ss%3Aword%21``.
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN",
        "postgresql://alice:p%40ss%3Aword%21@db.example.com:5432/orders",
    )
    with structlog.testing.capture_logs() as logs:
        src = SQLSource(_make_cfg())

    # Source initialised successfully.
    assert src._dialect == "postgresql"
    # No log line carries the cleartext password.
    flat = " ".join(repr(r) for r in logs)
    assert "p@ss:word!" not in flat
    assert "p%40ss%3Aword%21" not in flat


def test_dsn_password_with_colons_does_not_confuse_ipv6_bracket_logic(
    monkeypatch,
) -> None:
    """A password containing colons must not be mistaken for an IPv6 host.

    The bracket-wrap logic in __init__ wraps ``url.host`` (not the userinfo)
    when the host literal contains a colon; passing in a colon-rich password
    must not trip the heuristic.
    """
    import sys
    import types

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN",
        # Password = "a:b:c:d" (encoded colons)
        "postgresql://alice:a%3Ab%3Ac%3Ad@db.example.com:5432/orders",
    )
    src = SQLSource(_make_cfg())
    assert src._dialect == "postgresql"
    assert src._url.host == "db.example.com"


def test_dsn_with_url_encoded_password_special_chars_via_log_redaction() -> None:
    """End-to-end: encoded special-char passwords are scrubbed by log_redaction.

    The DSN userinfo regex must also handle the percent-encoded form, not
    just the cleartext form.
    """
    from urllib.parse import urlparse

    from recotem.log_redaction import _scrub_string_value

    dsn = "postgresql://alice:p%40ss%3Aword%21@db.example.com:5432/orders"
    out = _scrub_string_value(dsn)
    assert "p%40ss%3Aword%21" not in out
    assert "alice" not in out
    parsed = urlparse(out)
    assert parsed.hostname == "db.example.com"


# ---------------------------------------------------------------------------
# M-7 — query_parameters bind correctly for non-string types
# ---------------------------------------------------------------------------


def test_fetch_with_int_float_bool_parameters_bind_correctly(monkeypatch, tmp_path):
    """SQLAlchemy bindparams must round-trip int / float / bool values."""
    import sqlite3

    from recotem.datasource.base import FetchContext
    from recotem.datasource.sql import SQLConfig, SQLSource

    db = tmp_path / "params.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE events (user_id TEXT, item_id TEXT, ts TEXT, score REAL, sold INT)"
    )
    con.executemany(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        [
            ("u1", "i1", "2026-01-01", 3.14, 1),
            ("u2", "i2", "2026-01-02", 1.0, 0),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    cfg = SQLConfig(
        type="sql",
        dsn_env="RECOTEM_RECIPE_DB_DSN",
        query=(
            "SELECT user_id, item_id, ts FROM events "
            "WHERE score >= :min_score AND sold = :sold AND user_id != :exclude_user"
        ),
        query_parameters={
            "min_score": 1.5,  # float
            "sold": True,  # bool
            "exclude_user": "u2",  # str
        },
    )
    src = SQLSource(cfg)
    df = src.fetch(FetchContext(recipe_name="t", run_id="r"))
    assert list(df["user_id"]) == ["u1"]


# ---------------------------------------------------------------------------
# I-10 — TLS advisory warning
# ---------------------------------------------------------------------------


def test_tls_warning_postgres_without_sslmode(monkeypatch) -> None:
    """Postgres DSN without sslmode emits sql_dsn_tls_not_configured."""
    import sys
    import types

    import structlog

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@db.example.com/orders"
    )

    with structlog.testing.capture_logs() as logs:
        SQLSource(_make_cfg())

    events = [r for r in logs if r["event"] == "sql_dsn_tls_not_configured"]
    assert events, f"expected sql_dsn_tls_not_configured warning, got {logs!r}"
    assert events[0]["detected_sslmode"] == "(absent)"


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer"])
def test_tls_warning_postgres_with_weak_sslmode(monkeypatch, sslmode) -> None:
    """sslmode=disable/allow/prefer also warns — they permit plaintext."""
    import sys
    import types

    import structlog

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN",
        f"postgresql://u:p@db.example.com/orders?sslmode={sslmode}",
    )

    with structlog.testing.capture_logs() as logs:
        SQLSource(_make_cfg())

    events = [r for r in logs if r["event"] == "sql_dsn_tls_not_configured"]
    assert events, f"expected warning for sslmode={sslmode}"
    assert events[0]["detected_sslmode"] == sslmode


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_tls_warning_postgres_silent_with_strong_sslmode(monkeypatch, sslmode) -> None:
    """sslmode=require/verify-* is sufficient — no warning is emitted."""
    import sys
    import types

    import structlog

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN",
        f"postgresql://u:p@db.example.com/orders?sslmode={sslmode}",
    )

    with structlog.testing.capture_logs() as logs:
        SQLSource(_make_cfg())

    events = [r for r in logs if r["event"] == "sql_dsn_tls_not_configured"]
    assert not events, f"expected no TLS warning for sslmode={sslmode}, got {events!r}"


def test_tls_warning_mysql_without_ssl(monkeypatch) -> None:
    """MySQL DSN without ssl query param emits the TLS warning."""
    import sys
    import types

    import structlog

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setitem(sys.modules, "pymysql", types.ModuleType("pymysql"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "mysql+pymysql://u:p@db.example.com/orders"
    )

    with structlog.testing.capture_logs() as logs:
        SQLSource(_make_cfg())

    events = [r for r in logs if r["event"] == "sql_dsn_tls_not_configured"]
    assert events, f"expected sql_dsn_tls_not_configured warning, got {logs!r}"


def test_tls_warning_mysql_silent_with_ssl_true(monkeypatch) -> None:
    """MySQL DSN with ssl=true does not warn."""
    import sys
    import types

    import structlog

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setitem(sys.modules, "pymysql", types.ModuleType("pymysql"))
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN",
        "mysql+pymysql://u:p@db.example.com/orders?ssl=true",
    )

    with structlog.testing.capture_logs() as logs:
        SQLSource(_make_cfg())

    events = [r for r in logs if r["event"] == "sql_dsn_tls_not_configured"]
    assert not events


def test_tls_warning_silent_for_sqlite(monkeypatch) -> None:
    """SQLite has no network leg — no warning ever."""
    import structlog

    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")

    with structlog.testing.capture_logs() as logs:
        SQLSource(_make_cfg())

    events = [r for r in logs if r["event"] == "sql_dsn_tls_not_configured"]
    assert not events


def test_assert_host_public_returns_full_resolved_set(monkeypatch) -> None:
    """assert_host_public returns the entire resolved IP list, not just the first.

    Pre-fix this returned a single string and the SQL caller mis-classified
    legitimate dual-stack hostnames as rebinding attacks.
    """
    import socket
    from unittest.mock import patch

    from recotem._http_fetch import assert_host_public

    monkeypatch.delenv("RECOTEM_HTTP_ALLOW_PRIVATE", raising=False)

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, 0, 0, "", ("8.8.8.8", 0)),
            (socket.AF_INET6, 0, 0, "", ("2606:4700:4700::1111", 0)),
        ]

    with patch("socket.getaddrinfo", fake_getaddrinfo):
        result = assert_host_public("http://db.example.com/", allow_private=False)

    assert result == ["8.8.8.8", "2606:4700:4700::1111"]


# ---------------------------------------------------------------------------
# Coverage gap follow-up — PR #92 review:
# (1) chunksize clamps to cap when cap < 100_000;
# (2) full 127.0.0.0/8 + 0.0.0.0 SSRF coverage;
# (3) IDN/punycode hostname that resolves to a private IP is rejected.
# ---------------------------------------------------------------------------


def test_fetch_chunksize_clamped_to_cap_when_cap_below_100k(
    monkeypatch, tmp_path
) -> None:
    """When ``RECOTEM_MAX_SQL_ROWS`` < 100_000, ``pd.read_sql`` must be invoked
    with that smaller chunksize, not the 100_000 default.  Without the clamp,
    SQLite's full-result materialisation would buffer up to 99× more rows than
    the configured cap before the boundary check fires.
    """
    from unittest.mock import patch

    import recotem.datasource.sql as sql_mod
    from recotem.datasource.sql import SQLSource

    db = _seed_sqlite(tmp_path)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db}")
    src = SQLSource(_make_cfg(query="SELECT user_id, item_id, ts FROM events"))

    monkeypatch.setattr(sql_mod, "get_max_sql_rows", lambda: 1_000)

    real_read_sql = sql_mod.pd.read_sql
    captured_chunksize: list[int | None] = []

    def spy_read_sql(*args, **kwargs):
        captured_chunksize.append(kwargs.get("chunksize"))
        return real_read_sql(*args, **kwargs)

    with patch.object(sql_mod.pd, "read_sql", side_effect=spy_read_sql):
        src.fetch(_ctx())

    assert captured_chunksize, "pd.read_sql was not invoked"
    assert captured_chunksize[0] == 1_000, (
        f"expected chunksize=1000 (cap), got {captured_chunksize[0]}"
    )


@pytest.mark.parametrize(
    "private_ip",
    [
        "127.0.0.1",  # canonical loopback
        "127.0.0.2",  # mid-range loopback (127.0.0.0/8)
        "127.255.255.254",  # near-end loopback
        "0.0.0.0",  # unspecified / wildcard
    ],
)
def test_init_rejects_full_loopback_unspecified_range(monkeypatch, private_ip) -> None:
    """Every address in 127.0.0.0/8 plus 0.0.0.0 must trip the SSRF guard."""
    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"postgresql://u:p@{private_ip}/x")
    with pytest.raises(
        DataSourceError, match="(?i)private/loopback|RECOTEM_SQL_ALLOW_PRIVATE"
    ):
        SQLSource(_make_cfg())


def test_init_rejects_idn_hostname_resolving_to_private_ip(monkeypatch) -> None:
    """IDN/punycode hostnames that resolve to a private IP must trip SSRF guard.

    Confirms that hostname normalisation (URL parser → ``getaddrinfo``) does
    not bypass ``assert_host_public`` for non-ASCII hosts.  The test pins the
    resolver output so we don't depend on real DNS for the punycode form.
    """
    import socket
    import sys
    import types
    from unittest.mock import patch

    from recotem.datasource.sql import SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    # Punycode form of a non-ASCII hostname; SQLAlchemy's make_url will keep
    # it as-is, and our SSRF guard re-resolves it via getaddrinfo.
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@xn--exmple-cua.test/x"
    )

    def fake_getaddrinfo(host, port, *args, **kwargs):
        # Resolve any IDN host to a private RFC1918 address.
        return [(socket.AF_INET, 0, 0, "", ("10.0.0.5", 0))]

    with patch("socket.getaddrinfo", fake_getaddrinfo):
        with pytest.raises(
            DataSourceError, match="(?i)private/loopback|RECOTEM_SQL_ALLOW_PRIVATE"
        ):
            SQLSource(_make_cfg())
