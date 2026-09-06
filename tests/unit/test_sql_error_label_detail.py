"""``_error_label`` keeps SQLAlchemy's own diagnostic when there is no DBAPI error.

Measured defect: a ``mariadb+pymysql://`` DSN pointed at a real MySQL 8.4.11
server reported exactly

    probe failed for dialect 'mariadb': InvalidRequestError

and nothing more, from both ``recotem train`` and ``recotem validate``.
SQLAlchemy's message underneath was "MySQL version 8.4.11 is not a MariaDB
variant" — which names the problem precisely. ``_error_label`` discarded it
because ``_dbapi_error`` finds no ``orig`` (SQLAlchemy refuses at the dialect
layer, before any driver error exists), and the no-``orig`` branch returned a
bare class name.

The redaction rule the original code protects is *not* relaxed here: a driver
exception can embed DSN userinfo, and a driver exception always arrives with an
``orig``, so it still gets the class-name-and-SQLSTATE treatment.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import (
    ArgumentError,
    InvalidRequestError,
    OperationalError,
)

from recotem.datasource.sql import _error_label


class _FakeDBAPIError(Exception):
    """Stands in for a driver exception, whose __str__ may embed DSN userinfo."""

    def __init__(self, message: str, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def test_sqlalchemy_message_is_kept_when_there_is_no_dbapi_error() -> None:
    """The measured case: the one actionable sentence must survive."""
    exc = InvalidRequestError("MySQL version 8.4.11 is not a MariaDB variant.")
    label = _error_label(exc)
    assert "InvalidRequestError" in label
    assert "not a MariaDB variant" in label, (
        f"SQLAlchemy's diagnostic was discarded; got {label!r}"
    )


def test_bare_class_name_alone_is_not_enough() -> None:
    """Pin the regression shape directly.

    Before the fix this returned exactly 'InvalidRequestError'. Asserting on
    inequality with the class name means a revert fails here even if some other
    assertion is loosened.
    """
    exc = InvalidRequestError("MySQL version 8.4.11 is not a MariaDB variant.")
    assert _error_label(exc) != "InvalidRequestError"


def test_driver_exception_still_gets_only_class_name_and_sqlstate() -> None:
    """The security rule is unchanged for exceptions that carry a DBAPI error.

    A driver message can embed DSN userinfo, so it must stay out of the label.
    """
    orig = _FakeDBAPIError(
        "connection to postgresql://admin:hunter2@db.internal/app failed",
        sqlstate="28P01",
    )
    exc = OperationalError("stmt", {}, orig)
    label = _error_label(exc)
    assert "hunter2" not in label, f"driver message leaked into the label: {label!r}"
    assert "admin" not in label
    assert "SQLSTATE 28P01" in label
    assert "_FakeDBAPIError" in label


def test_userinfo_is_stripped_even_from_a_sqlalchemy_message() -> None:
    """Belt and braces: SQLAlchemy's own text is redacted too.

    Nothing observed puts a password in a no-``orig`` SQLAlchemy message, but
    the sample of exception types is small and the redaction costs nothing.
    """
    exc = ArgumentError(
        "Could not parse URL postgresql+psycopg://admin:hunter2@db.internal/app"
    )
    label = _error_label(exc)
    assert "hunter2" not in label, f"userinfo survived redaction: {label!r}"
    assert "admin" not in label
    assert "***@" in label
    assert "db.internal" in label, "the host is the useful half; keep it"


def test_long_sqlalchemy_message_is_bounded() -> None:
    """A message quoting a large statement must not flood the log line."""
    exc = InvalidRequestError("x" * 5000)
    label = _error_label(exc)
    assert len(label) < 300, f"label grew to {len(label)} chars"
    assert label.endswith("…")


def test_non_sqlalchemy_exception_without_orig_is_unchanged() -> None:
    """Only SQLAlchemy's own diagnostics are surfaced.

    A bare driver exception raised outside a SQLAlchemy wrapper has no ``orig``
    either, and its message is exactly the kind that can carry DSN userinfo, so
    it must keep the class-name-only treatment.
    """
    exc = _FakeDBAPIError("connect to postgresql://admin:hunter2@h/db failed")
    label = _error_label(exc)
    assert label == "_FakeDBAPIError"
    assert "hunter2" not in label


@pytest.mark.parametrize("message", ["", "   ", "\n"])
def test_empty_sqlalchemy_message_falls_back_to_the_class_name(message: str) -> None:
    """No message means nothing to add; do not emit a dangling separator."""
    exc = InvalidRequestError(message)
    assert _error_label(exc) == "InvalidRequestError"


def test_probe_failure_against_a_mismatched_server_is_actionable() -> None:
    """End-to-end through the message the operator actually reads.

    ``probe()`` wraps the label in "probe failed for dialect ...". This asserts
    the whole rendered string, which is what appears on the terminal and in the
    train_error JSON line.
    """
    exc = InvalidRequestError("MySQL version 8.4.11 is not a MariaDB variant.")
    rendered = f"probe failed for dialect 'mariadb': {_error_label(exc)}"
    assert "not a MariaDB variant" in rendered
    assert rendered != "probe failed for dialect 'mariadb': InvalidRequestError"
