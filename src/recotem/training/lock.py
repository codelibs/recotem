"""Per-recipe file locking.

Provides ``recipe_lock(path, ...)`` which returns a context manager that
acquires an exclusive file lock at ``<path>.lock``.

Implements the spec's lock semantics (Section 6 step 2):
- Default: exclusive mode (LOCK_EX).
- If lock is contended (non-blocking acquire fails): yield False so the
  caller can exit 0 gracefully (default), or raise ``LockContestedError``
  when ``fail_on_busy=True``.
- ``--no-lock`` is expressed by callers simply not calling this module.

Uses ``fcntl.flock`` on POSIX and falls back to a best-effort open-based
lock on Windows.  The spec targets Linux/macOS (Docker), so POSIX is primary.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


class LockContestedError(Exception):
    """Raised when the recipe lock is held by another process and
    ``fail_on_busy=True`` was requested."""

    code = "lock_contested"


_LOCAL_SCHEMES = {"", "file"}


@contextlib.contextmanager
def recipe_lock(
    output_path: str | Path,
    *,
    exclusive: bool = True,
    fail_on_busy: bool = False,
    timeout: float = 0.0,
):
    """Context manager that acquires a per-recipe file lock.

    The lock file is created at ``<output_path>.lock``.  The directory of
    *output_path* must already exist (or will be created).

    Parameters
    ----------
    output_path:
        The artifact output path from ``recipe.output.path``.
    exclusive:
        If ``True`` (default), acquire an exclusive write lock.
    fail_on_busy:
        If ``True``, raise ``LockContestedError`` when the lock is held.
        If ``False`` (default), yields ``False`` so the caller exits 0.
    timeout:
        Seconds to wait for the lock.  ``0.0`` = non-blocking (default).
        ``-1`` = wait indefinitely.

    Yields
    ------
    bool
        ``True`` if the lock was acquired; ``False`` if contended and
        ``fail_on_busy=False``.

    Raises
    ------
    LockContestedError
        Only when *fail_on_busy* is ``True`` and the lock cannot be acquired.
    """
    output_str = str(output_path)
    scheme = urlparse(output_str).scheme.lower() if "://" in output_str else ""
    if scheme not in _LOCAL_SCHEMES:
        # ``flock`` is a host-local primitive. For remote outputs the lock
        # file is created at a host-local path derived from the URI and
        # cannot coordinate writers running on different hosts/pods. Emit
        # a structured warning so operators don't assume cross-host mutual
        # exclusion. See docs/operations.md "Concurrent training" section.
        logger.warning(
            "recipe_lock_local_only",
            scheme=scheme,
            output_path=output_str,
            advice=(
                "per-recipe flock is host-local; ensure single-writer via the "
                "scheduler (CronJob concurrencyPolicy=Forbid, Argo mutex, etc.)"
            ),
        )

    lock_path = Path(output_str + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        acquired = _try_acquire_windows(lock_path)
        if not acquired:
            if fail_on_busy:
                raise LockContestedError(
                    f"Recipe lock at {lock_path} is held by another process."
                )
            yield False
            return
        try:
            yield True
        finally:
            with contextlib.suppress(OSError):
                lock_path.unlink(missing_ok=True)
        return

    # POSIX path via fcntl.flock
    import fcntl  # noqa: PLC0415 (POSIX only)

    lock_op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if timeout == 0.0:
        lock_op |= fcntl.LOCK_NB  # non-blocking

    fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        try:
            if timeout > 0:
                # Polling loop for a timed acquire.
                import time  # noqa: PLC0415

                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fd, lock_op | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
            else:
                fcntl.flock(fd, lock_op)
        except OSError as exc:
            # Lock contended (LOCK_NB + EWOULDBLOCK / EACCES).
            if fail_on_busy:
                raise LockContestedError(
                    f"Recipe lock at {lock_path} is held by another process. "
                    "Pass --fail-on-busy to surface this as an error, or wait."
                ) from exc
            yield False
            return

        yield True

        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            lock_path.unlink(missing_ok=True)


def _try_acquire_windows(lock_path: Path) -> bool:
    """Best-effort exclusive lock via exclusive file creation on Windows."""
    try:
        fd = os.open(
            str(lock_path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        os.close(fd)
        return True
    except FileExistsError:
        return False
