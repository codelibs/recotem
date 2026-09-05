"""Guards that the SQL driver preflight checks the driver the DSN will use.

The probe used to be keyed off the recotem *extra* rather than the DSN. That
validated a driver the URL was never going to load: `recotem[postgres]`
installs psycopg v3, the probe imported `psycopg` and passed, and SQLAlchemy
then loaded its DEFAULT PostgreSQL DBAPI -- psycopg2, which recotem never
installs. Every bare `postgresql://` DSN therefore died at DBAPI-import time,
before any network I/O, with a bare `ModuleNotFoundError` naming neither the
missing module nor the fix.

The bare scheme is the form every operator knows, and recotem's own "DSN not
set" message used to suggest it. Four of the schemes an operator might
reasonably write route to a driver recotem does not install:

    postgresql://   -> psycopg2   (not installed)
    postgres://     -> no dialect at all (removed in SQLAlchemy 2.x)
    mysql://        -> mysqldb    (not installed)
    mariadb://      -> mysqldb    (not installed)

Only `sqlite://` works bare, because its DBAPI is in the stdlib.
"""

from __future__ import annotations

import sys

import pytest
from sqlalchemy.engine.url import make_url

from recotem.datasource.base import DataSourceError
from recotem.datasource.sql import SQLConfig, SQLSource

# The module-level tables this fix introduces are imported INSIDE the two
# structural tests below, never at module scope.  Importing them here would
# turn a revert into a collection error that takes the whole file down with
# one ImportError -- which proves the constant is gone, not that the behaviour
# regressed.  Every behavioural test below must fail on its own assertion.


def _cfg() -> SQLConfig:
    return SQLConfig(
        type="sql",
        dsn_env="RECOTEM_RECIPE_DB_DSN",
        query="SELECT user_id, item_id FROM t",
    )


@pytest.fixture(autouse=True)
def _allow_private(monkeypatch: pytest.MonkeyPatch):
    """Keep the SSRF guard out of the way; these tests are about drivers."""
    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")


@pytest.mark.parametrize(
    ("dsn", "driver", "recommended"),
    [
        ("postgresql://u:p@h/db", "psycopg2", "postgresql+psycopg://"),
        ("postgresql+psycopg2://u:p@h/db", "psycopg2", "postgresql+psycopg://"),
        ("mysql://u:p@h/db", "mysqldb", "mysql+pymysql://"),
        ("mariadb://u:p@h/db", "mysqldb", "mariadb+pymysql://"),
    ],
)
def test_dsn_routing_to_an_uninstalled_driver_is_refused_with_the_fix(
    monkeypatch: pytest.MonkeyPatch, dsn: str, driver: str, recommended: str
) -> None:
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", dsn)
    with pytest.raises(DataSourceError) as exc:
        SQLSource(_cfg())

    message = str(exc.value)
    assert driver in message, message
    # The remedy an operator can act on is the DSN spelling, not the extra:
    # installing the extra does NOT make the bare scheme work.
    assert recommended in message, message


def test_bare_scheme_is_explained_as_a_default_not_a_typo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@h/db")
    with pytest.raises(DataSourceError) as exc:
        SQLSource(_cfg())
    assert "no +driver suffix" in str(exc.value)


def test_explicit_driver_is_reported_as_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DSN that names psycopg2 outright is a different mistake from a default."""
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "postgresql+psycopg2://u:p@h/db")
    with pytest.raises(DataSourceError) as exc:
        SQLSource(_cfg())
    assert "explicitly" in str(exc.value)


def test_postgres_alias_is_refused_with_the_working_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`postgres://` cannot be rescued by any +driver suffix.

    SQLAlchemy 2.x removed the dialect, so this must NOT be advertised as a
    supported dialect -- which is what listing it in `_DIALECT_TO_EXTRA` did.
    """
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "postgres://u:p@h/db")
    with pytest.raises(DataSourceError) as exc:
        SQLSource(_cfg())

    message = str(exc.value)
    assert "SQLAlchemy 2.x" in message, message
    assert "postgresql+psycopg://" in message, message


def test_postgres_is_no_longer_advertised_as_supported() -> None:
    from recotem.datasource.sql import _DIALECT_TO_EXTRA

    assert "postgres" not in _DIALECT_TO_EXTRA


def test_unsupported_dialect_lists_dsn_forms_not_extra_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old message listed extras ('mysql', 'postgres', 'sqlite') under the
    word "dialect" -- wrong vocabulary, and one of the three could never work.
    """
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "oracle://u:p@h/db")
    with pytest.raises(DataSourceError) as exc:
        SQLSource(_cfg())

    message = str(exc.value)
    assert "postgresql+psycopg://" in message, message
    assert "'postgres'" not in message, message


@pytest.mark.parametrize(
    "dsn",
    ["postgresql+psycopg://u:p@h/db", "mysql+pymysql://u:p@h/db"],
)
def test_the_recommended_forms_construct(
    monkeypatch: pytest.MonkeyPatch, dsn: str
) -> None:
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", dsn)
    SQLSource(_cfg())  # must not raise


def test_missing_extra_still_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """The original purpose of the probe must survive the rewrite.

    A correctly-spelled DSN whose driver genuinely is not installed still has
    to point at `pip install 'recotem[...]'`.
    """
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(DataSourceError, match=r"recotem\[postgres\]"):
        SQLSource(_cfg())


def test_driver_check_precedes_dns_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering guard: a missing driver must not be reported as a DNS failure.

    The driver probe is a pure local import check that raises before any
    connection, so putting it ahead of the SSRF/DNS guard cannot weaken that
    guard. Putting it after does hurt: offline and in CI -- where a missing
    extra is actually met -- the unresolvable host would be reported instead.
    """
    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv(
        "RECOTEM_RECIPE_DB_DSN", "postgresql://u:p@nonexistent.invalid/db"
    )
    with pytest.raises(DataSourceError) as exc:
        SQLSource(_cfg())

    message = str(exc.value)
    assert "psycopg2" in message, message
    assert "does not resolve" not in message, message


def test_driver_module_table_matches_sqlalchemy_driver_names() -> None:
    """Pin the driver names SQLAlchemy actually derives from these schemes.

    The table is only correct as long as SQLAlchemy keeps choosing these
    defaults; a change upstream must fail here rather than silently restore the
    unactionable ModuleNotFoundError.
    """
    from recotem.datasource.sql import _DRIVER_MODULE

    observed = {
        make_url(f"{scheme}u:p@h/db").get_driver_name()
        for scheme in ("postgresql://", "mysql://", "mariadb://")
    }
    assert observed == {"psycopg2", "mysqldb"}
    assert set(observed) <= set(_DRIVER_MODULE)


def test_every_supported_backend_has_a_recommended_dsn() -> None:
    from recotem.datasource.sql import _BACKEND_RECOMMENDED_DSN, _DIALECT_TO_EXTRA

    assert set(_DIALECT_TO_EXTRA) <= set(_BACKEND_RECOMMENDED_DSN)
