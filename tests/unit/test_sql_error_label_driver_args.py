"""``_error_label`` keeps a driver's *argument validation* message too.

Sibling of ``test_sql_error_label_detail.py``, which covers the SQLAlchemy half
of the same no-``orig`` branch.

Measured defect: two failures an operator can reach from the DSN alone reported
one word each, through the real CLI, with no server involved --

    probe failed for dialect 'mysql': ValueError
    probe failed for dialect 'mysql': FileNotFoundError

-- while the messages underneath said ``port should be of type int`` and
``[Errno 2] No such file or directory``.  Both are raised by PyMySQL (or by
``ssl``) while it is still checking the keyword arguments SQLAlchemy assembled,
before a socket exists, so neither is a DBAPI error and neither reaches the
``orig`` branch that the userinfo rule is written for.

The rule itself is not relaxed: a *database* error still gets the
class-name-and-SQLSTATE treatment, and an exception type outside
``_SAFE_DETAIL_TYPES`` still gets the class name alone.
"""

from __future__ import annotations

import pytest

from recotem.datasource.base import DataSourceError
from recotem.datasource.sql import (
    _MAX_SA_DETAIL,
    SQLConfig,
    SQLSource,
    _error_label,
)

# Present in the DSN's userinfo *and* in a ``?password=`` query value, so a
# leak through either route fails the assertion.
_SECRET = "hunter2SECRET"


def _probe(monkeypatch: pytest.MonkeyPatch, dsn: str) -> str:
    """Run ``SQLSource(...).probe()`` and return the DataSourceError message.

    Loopback host and a driver that refuses the arguments before it opens a
    socket, so this touches no network and needs no server.
    """
    monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("RECOTEM_RECIPE_TEST_DSN", dsn)
    src = SQLSource(
        SQLConfig(type="sql", dsn_env="RECOTEM_RECIPE_TEST_DSN", query="SELECT 1")
    )
    with pytest.raises(DataSourceError) as excinfo:
        src.probe()
    return str(excinfo.value)


def test_query_string_port_names_the_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``?port=`` that routes the connection must not report the word 'ValueError'.

    SQLAlchemy's MySQL dialect coerces the *netloc* port to ``int`` but passes a
    query-string one through as the ``str`` it was written as, and PyMySQL
    refuses it.  ``?host=`` is a routing form the SSRF guard enumerates, so an
    operator using it needs ``?port=`` alongside whenever the port is not 3306.
    """
    pytest.importorskip("pymysql")
    msg = _probe(
        monkeypatch,
        f"mysql+pymysql:///db?host=127.0.0.1&port=3306x&user=u&password={_SECRET}",
    )
    assert "port should be of type int" in msg, msg
    assert msg != "probe failed for dialect 'mysql': ValueError"
    assert _SECRET not in msg


def test_missing_ssl_ca_names_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ssl_ca`` pointing at nothing must not report the word 'FileNotFoundError'."""
    pytest.importorskip("pymysql")
    msg = _probe(
        monkeypatch,
        f"mysql+pymysql://u:{_SECRET}@127.0.0.1:3306/db"
        "?ssl_ca=/nonexistent/recotem-test-ca.pem",
    )
    assert "No such file or directory" in msg, msg
    assert msg != "probe failed for dialect 'mysql': FileNotFoundError"
    assert _SECRET not in msg


def test_userinfo_inside_such_a_message_is_redacted() -> None:
    """Belt and braces for the redaction, exercised directly.

    No exception reachable through this branch was found to embed a URL --
    SQLAlchemy hands the driver ``user=`` and ``password=`` as separate keyword
    arguments, so no URL exists where these are raised.  That makes this input
    synthetic on purpose: it is the only way to show the redaction is armed
    rather than merely unexercised.
    """
    exc = ValueError("cannot reach mysql+pymysql://admin:hunter2@db.internal/app")
    label = _error_label(exc)
    assert "hunter2" not in label, f"userinfo survived redaction: {label!r}"
    assert "admin" not in label
    assert "***@" in label
    assert "db.internal" in label, "the host is the useful half; keep it"


def test_such_a_message_is_bounded() -> None:
    """A pathological driver message must not flood the log line."""
    label = _error_label(OSError("x" * 5000))
    assert len(label) < _MAX_SA_DETAIL + 100, f"label grew to {len(label)} chars"
    assert label.endswith("…")


def test_a_type_outside_the_allow_list_still_reports_the_class_name() -> None:
    """The allow-list is closed, and this is the boundary.

    Widening ``_SAFE_DETAIL_TYPES`` to something like ``(Exception,)`` would
    surface the message of any exception that happens to arrive without an
    ``orig`` -- which is the treatment the userinfo rule exists to withhold.
    """
    exc = RuntimeError("connect to postgresql://admin:hunter2@h/db failed")
    assert _error_label(exc) == "RuntimeError"
    assert "hunter2" not in _error_label(exc)


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_empty_message_falls_back_to_the_class_name(empty: str) -> None:
    """No message means nothing to add; do not emit a dangling separator."""
    assert _error_label(ValueError(empty)) == "ValueError"
