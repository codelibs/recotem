"""End-to-end exit-code checks for ``recotem serve`` bind failures.

These tests run a **real** ``recotem serve`` process against a **real** uvicorn
so that the documented exit code (8 = ``_EXIT_CONFIG``) is verified against
uvicorn's actual behaviour rather than a mock.

Why this file exists
--------------------
``uvicorn.Server.startup`` catches the bind ``OSError`` itself, logs it, and
calls ``sys.exit(uvicorn.config.STARTUP_FAILURE)`` (== 3).  ``SystemExit``
derives from ``BaseException``, so it bypasses ``except OSError`` and
``except Exception`` in ``recotem.cli.serve``.  Before the fix, every bind
failure exited with uvicorn's raw 3 — which collides with ``_EXIT_DATASOURCE``
and misleads supervisor/CronJob retry logic.

The unit tests for this path necessarily patch ``uvicorn.run``; a patched
``uvicorn.run`` can assert whatever the author believes uvicorn does.  That is
exactly how the original bug survived: the old unit test mocked
``side_effect=OSError(EADDRINUSE)``, a behaviour real uvicorn never exhibits.
Only a real subprocess can catch a regression in that assumption, so these
tests deliberately spend a few seconds each.

Covered bind errnos: EADDRINUSE, EACCES, EADDRNOTAVAIL.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Exit code the CLI must report for any bind / startup configuration failure.
_EXPECTED_BIND_EXIT = 8

# Generous enough for interpreter start + FastAPI import on a loaded CI box,
# short enough that a hang fails the suite rather than stalling it.
_SUBPROCESS_TIMEOUT = 120.0

# RFC 5737 TEST-NET-1.  Never assigned to a local interface, so binding it
# yields EADDRNOTAVAIL on both Linux and macOS.  (127.0.0.2 would not work:
# Linux treats all of 127.0.0.0/8 as local and the bind would succeed.)
_UNASSIGNABLE_HOST = "192.0.2.1"


@pytest.fixture
def recipes_dir(tmp_path: Path) -> Path:
    """An empty recipes directory — startup must reach the bind stage fast."""
    d = tmp_path / "recipes"
    d.mkdir()
    return d


def _serve_env(**overrides: str) -> dict[str, str]:
    """Base environment for a serve subprocess.

    ``RECOTEM_ENV=test`` plus ``--insecure-no-auth`` keeps the run from being
    rejected by the auth posture gate before it ever reaches the bind.
    """
    env = dict(os.environ)
    env.update(
        {
            "RECOTEM_ENV": "test",
            "RECOTEM_SIGNING_KEYS": "active:" + ("ab" * 32),
            "RECOTEM_LOG_FORMAT": "json",
        }
    )
    env.pop("RECOTEM_API_KEYS", None)
    env.update(overrides)
    return env


def _run_serve(recipes: Path, host: str, port: int) -> subprocess.CompletedProcess:
    """Run ``recotem serve`` to completion and return the finished process.

    Invoked as ``python -m recotem.cli`` so the test does not depend on the
    ``recotem`` console script being on PATH.
    """
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "recotem.cli",
            "serve",
            "--recipes",
            str(recipes),
            "--host",
            host,
            "--port",
            str(port),
            "--insecure-no-auth",
        ],
        env=_serve_env(),
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        check=False,
    )


@pytest.fixture
def occupied_port() -> Iterator[int]:
    """Bind an ephemeral port and hold it for the duration of the test."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        yield sock.getsockname()[1]
    finally:
        sock.close()


def _assert_bind_exit(proc: subprocess.CompletedProcess, label: str) -> None:
    combined = proc.stdout + proc.stderr
    assert proc.returncode == _EXPECTED_BIND_EXIT, (
        f"{label}: expected exit {_EXPECTED_BIND_EXIT} (_EXIT_CONFIG), got "
        f"{proc.returncode}.\n"
        f"NOTE: exit 3 means uvicorn's SystemExit(STARTUP_FAILURE) leaked "
        f"through recotem.cli.serve again (3 collides with _EXIT_DATASOURCE).\n"
        f"--- output ---\n{combined}"
    )
    assert "serve_bind_failed" in combined, (
        f"{label}: expected a serve_bind_failed log event.\n--- output ---\n{combined}"
    )


def test_serve_bind_eaddrinuse_exits_8(recipes_dir: Path, occupied_port: int) -> None:
    """A port already held by another socket must exit 8, not uvicorn's 3."""
    proc = _run_serve(recipes_dir, "127.0.0.1", occupied_port)
    _assert_bind_exit(proc, "EADDRINUSE")


def test_serve_bind_eaddrnotavail_exits_8(recipes_dir: Path) -> None:
    """An address not assigned to any local interface must exit 8."""
    proc = _run_serve(recipes_dir, _UNASSIGNABLE_HOST, 18080)
    _assert_bind_exit(proc, "EADDRNOTAVAIL")


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root may bind privileged ports, so EACCES cannot be provoked",
)
@pytest.mark.skipif(
    not hasattr(os, "geteuid"),
    reason="privileged-port semantics are POSIX-specific",
)
def test_serve_bind_eacces_exits_8(recipes_dir: Path) -> None:
    """Binding a privileged port as a non-root user must exit 8."""
    proc = _run_serve(recipes_dir, "127.0.0.1", 80)
    _assert_bind_exit(proc, "EACCES")
