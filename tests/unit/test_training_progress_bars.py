"""Unit tests for the fastprogress console-bar policy in training._compat.

irspack's early-stopping recommenders draw a fastprogress bar directly onto
stdout, and fastprogress's own gate is broken: ``printing()`` evaluates
``getattr(stdout, 'isatty', False)``, which yields the bound method instead
of calling it, so the guard is truthy for pipes and files alike.  A redirected
``recotem train`` therefore captured kilobytes of carriage returns and block
characters while every structured log event went to stderr.

Tests:
- the predicate suppresses on a non-TTY stdout, --quiet, and explicit
  RECOTEM_LOG_FORMAT=json, and leaves an interactive run alone
- a missing / closed sys.stdout resolves to "no terminal", not an exception
- suppress_progress_bars writes all three fastprogress globals, is idempotent,
  and is a no-op on an interactive stdout
- the behavioural check: driving a real ConsoleProgressBar writes nothing to
  stdout after suppression, and does write without it

NOTE: ``sys.stdout`` is patched inside each test body, never from a fixture.
pytest's capture manager re-installs its own ``sys.stdout`` when it resumes
capturing for the call phase, which would silently undo a setup-phase patch.
"""

from __future__ import annotations

import io
import sys
from collections.abc import Iterator
from contextlib import redirect_stdout

import pytest

from recotem.training._compat import (
    should_suppress_progress_bars,
    suppress_progress_bars,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStdout:
    """Minimal stdout stand-in whose TTY-ness the test dictates."""

    def __init__(self, *, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _pretend_stdout_is_a_tty(monkeypatch: pytest.MonkeyPatch, *, tty: bool) -> None:
    """Fix both inputs to the predicate.

    Set explicitly rather than relying on how pytest was invoked: under
    ``-s`` the real stdout *is* a TTY and the captured stream is not, so an
    unpinned test would assert different things in the two modes.
    """
    monkeypatch.setattr(sys, "stdout", _FakeStdout(tty=tty))
    monkeypatch.delenv("RECOTEM_LOG_FORMAT", raising=False)


def _reset_fastprogress_to_stock() -> None:
    """Put fastprogress back into the state a fresh import leaves it in.

    Another test module that ran ``run_training`` will already have applied
    the suppression process-wide, so tests asserting the unsuppressed
    behaviour have to undo it first.
    """
    from fastprogress import fastprogress as fp

    fp.NO_BAR = False
    fp.WRITER_FN = print
    vars(fp).pop("print", None)


@pytest.fixture(autouse=True)
def _restore_fastprogress_globals() -> Iterator[None]:
    """Undo any suppression so the globals never leak into another test.

    ``suppress_progress_bars`` mutates module state in a third-party package;
    without this the first test to call it would silence bars for the rest of
    the session and mask a regression in the "interactive is untouched" case.
    """
    from fastprogress import fastprogress as fp

    saved_no_bar = fp.NO_BAR
    saved_writer = fp.WRITER_FN
    had_print = "print" in vars(fp)
    saved_print = vars(fp).get("print")
    try:
        yield
    finally:
        fp.NO_BAR = saved_no_bar
        fp.WRITER_FN = saved_writer
        if had_print:
            fp.print = saved_print  # type: ignore[attr-defined]
        else:
            vars(fp).pop("print", None)


# ---------------------------------------------------------------------------
# Predicate
# ---------------------------------------------------------------------------


def test_non_tty_stdout_suppresses(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirected or piped stdout is the case this whole guard exists for."""
    _pretend_stdout_is_a_tty(monkeypatch, tty=False)

    assert should_suppress_progress_bars() is True


def test_interactive_stdout_does_not_suppress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Someone at a terminal must keep seeing progress."""
    _pretend_stdout_is_a_tty(monkeypatch, tty=True)

    assert should_suppress_progress_bars() is False


def test_quiet_suppresses_even_on_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """--quiet asks for per-trial output to stop; a per-trial bar is that."""
    _pretend_stdout_is_a_tty(monkeypatch, tty=True)

    assert should_suppress_progress_bars(quiet=True) is True


def test_json_log_format_suppresses_even_on_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_LOG_FORMAT=json declares a machine-read run (compose sets it)."""
    _pretend_stdout_is_a_tty(monkeypatch, tty=True)
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", "json")

    assert should_suppress_progress_bars() is True


@pytest.mark.parametrize("value", ["auto", "console", "", "  "])
def test_non_json_log_format_leaves_a_tty_alone(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Only an explicit ``json`` counts — ``auto`` resolves off stderr."""
    _pretend_stdout_is_a_tty(monkeypatch, tty=True)
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", value)

    assert should_suppress_progress_bars() is False


def test_absent_stdout_is_treated_as_no_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sys.stdout is None`` (no console attached) must not raise."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.delenv("RECOTEM_LOG_FORMAT", raising=False)

    assert should_suppress_progress_bars() is True


def test_closed_stdout_is_treated_as_no_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed stream raises ValueError from isatty(); that means "no TTY"."""
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stdout", closed)
    monkeypatch.delenv("RECOTEM_LOG_FORMAT", raising=False)

    assert should_suppress_progress_bars() is True


# ---------------------------------------------------------------------------
# Applying the policy to fastprogress
# ---------------------------------------------------------------------------


def test_suppress_sets_every_fastprogress_write_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three globals must move: NO_BAR alone leaves two paths writing."""
    from fastprogress import fastprogress as fp

    _pretend_stdout_is_a_tty(monkeypatch, tty=False)
    _reset_fastprogress_to_stock()

    assert suppress_progress_bars() is True

    assert fp.NO_BAR is True
    assert fp.WRITER_FN is not print
    # ConsoleProgressBar.__init__ probes the encoding with a bare print()
    # before any gate is consulted; only a module-global shadow reaches it.
    assert vars(fp).get("print") not in (None, print)


def test_suppress_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-applying must not stack shims, mirroring _ipython_stub.install."""
    from fastprogress import fastprogress as fp

    _pretend_stdout_is_a_tty(monkeypatch, tty=False)

    assert suppress_progress_bars() is True
    first_writer = fp.WRITER_FN
    first_print = vars(fp)["print"]

    assert suppress_progress_bars() is True

    assert fp.WRITER_FN is first_writer
    assert vars(fp)["print"] is first_print


def test_suppress_leaves_fastprogress_untouched_when_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interactive path must not be degraded by the fix."""
    from fastprogress import fastprogress as fp

    _pretend_stdout_is_a_tty(monkeypatch, tty=True)
    _reset_fastprogress_to_stock()

    assert suppress_progress_bars() is False

    assert fp.NO_BAR is False
    assert fp.WRITER_FN is print
    assert "print" not in vars(fp)


# ---------------------------------------------------------------------------
# Behavioural: drive a real bar and watch stdout
# ---------------------------------------------------------------------------


def _drive_a_bar() -> str:
    """Iterate a real ConsoleProgressBar and return everything it wrote."""
    from fastprogress.fastprogress import ConsoleProgressBar

    sink = io.StringIO()
    with redirect_stdout(sink):
        for _ in ConsoleProgressBar(range(4), leave=False):
            pass
    return sink.getvalue()


def test_unsuppressed_bar_writes_carriage_returns_to_stdout() -> None:
    """Pin the defect: fastprogress writes to a non-TTY stdout regardless.

    ``redirect_stdout`` installs a StringIO, whose ``isatty()`` is False, and
    the bar renders into it anyway — which is exactly why the pipeline has to
    intervene rather than trusting fastprogress's own gate.
    """
    _reset_fastprogress_to_stock()

    written = _drive_a_bar()

    assert "\r" in written
    assert "█" in written


def test_suppressed_bar_writes_nothing_to_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After suppression a full bar lifecycle must leave stdout empty."""
    _pretend_stdout_is_a_tty(monkeypatch, tty=False)

    assert suppress_progress_bars() is True

    written = _drive_a_bar()

    assert written == "", f"progress output leaked to stdout: {written!r}"
