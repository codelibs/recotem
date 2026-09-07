"""The TLS hints must keep naming the parameters that complete a connection.

``sql_dsn_tls_not_configured`` is prescriptive: an operator reads ``hint`` and
copies the query parameters out of it.  Naming the strict spelling without the
parameter it depends on sends them from a DSN that connects to one that cannot,
which is what #371 fixed -- ``verify-ca`` / ``verify-full`` need
``sslrootcert``, and ``ssl_ca`` against a server presenting the certificate it
generated for itself needs ``ssl_check_hostname=false``.

That fix was two string constants and nothing pinned them: reverting either
hint, or emitting the postgres hint on the mysql branch, left the whole suite
green.  These tests assert on the emitted log field -- a behaviour of the code,
not a sentence in a document -- so a refactor or a merge-conflict resolution
that drops a parameter fails here instead of shipping.
"""

from __future__ import annotations

import sys
import types

import pytest
import structlog


def _cfg():
    from recotem.datasource.sql import SQLConfig

    return SQLConfig(
        type="sql",
        dsn_env="RECOTEM_RECIPE_DB_DSN",
        query="SELECT user_id, item_id, ts FROM events",
    )


def _hint_for(monkeypatch, dsn: str, driver: str) -> str:
    from recotem.datasource.sql import SQLSource

    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setitem(sys.modules, driver, types.ModuleType(driver))
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", dsn)

    with structlog.testing.capture_logs() as logs:
        SQLSource(_cfg())

    events = [r for r in logs if r["event"] == "sql_dsn_tls_not_configured"]
    assert events, f"expected sql_dsn_tls_not_configured, got {logs!r}"
    return events[0]["hint"]


# Each entry is (parameter, why the hint is wrong without it).
_PG_REQUIRED = [
    ("sslmode=require", "the mode that forces TLS at all"),
    (
        "sslrootcert",
        "verify-ca / verify-full look for ~/.postgresql/root.crt without it "
        "and refuse the connection when that file is absent",
    ),
]

_MYSQL_REQUIRED = [
    ("ssl_ca=", "the parameter that forces TLS"),
    (
        "&ssl_check_hostname=false",
        "MySQL's self-generated certificate names no host, so ssl_ca alone "
        "fails the handshake -- this is the parameter added beside it",
    ),
    (
        "?ssl_check_hostname=false",
        "MariaDB writes no ca.pem, so there is no file for ssl_ca to name and "
        "this spelling on its own is the only one that connects",
    ),
]


@pytest.mark.parametrize(("parameter", "why"), _PG_REQUIRED)
def test_postgres_tls_hint_names_the_parameter(monkeypatch, parameter, why) -> None:
    hint = _hint_for(
        monkeypatch, "postgresql+psycopg://u:p@db.example.com/orders", "psycopg"
    )
    assert parameter in hint, (
        f"the postgres TLS hint no longer names {parameter!r}: {why}.\n"
        f"hint was: {hint!r}"
    )


@pytest.mark.parametrize(("parameter", "why"), _MYSQL_REQUIRED)
def test_mysql_tls_hint_names_the_parameter(monkeypatch, parameter, why) -> None:
    hint = _hint_for(
        monkeypatch, "mysql+pymysql://u:p@db.example.com/orders", "pymysql"
    )
    assert parameter in hint, (
        f"the mysql/mariadb TLS hint no longer names {parameter!r}: {why}.\n"
        f"hint was: {hint!r}"
    )


def test_each_dialect_gets_its_own_hint(monkeypatch) -> None:
    """The two hints must not be swapped.

    Both are non-empty prescriptive strings, so a wiring mistake that emits one
    on the other's branch is invisible to a "hint is present" check: the
    operator is handed parameters their driver does not accept.
    """
    pg = _hint_for(
        monkeypatch, "postgresql+psycopg://u:p@db.example.com/orders", "psycopg"
    )
    my = _hint_for(monkeypatch, "mysql+pymysql://u:p@db.example.com/orders", "pymysql")
    assert "sslmode" in pg and "ssl_ca" not in pg, (
        f"the postgres branch emitted a non-postgres hint: {pg!r}"
    )
    assert "ssl_ca" in my and "sslmode" not in my, (
        f"the mysql branch emitted a non-mysql hint: {my!r}"
    )
