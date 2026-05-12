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

Lock-file sentinel pattern
--------------------------
The ``.lock`` file is intentionally **never deleted**.  Deleting it while a
holder still has the fd open creates a classic inode-rotation race:

1. Holder opens inode A, acquires flock.
2. Contender opens inode A, tries flock → blocked / EWOULDBLOCK.
3. Holder closes fd → flock released.  Contender is about to call flock …
4. A third process deletes inode A and creates inode B at the same path.
5. Contender calls flock on the *old* inode A (already unlinked).
6. Third process opens inode B and acquires flock on it.

Both the contender (inode A) and the third process (inode B) now each believe
they hold "the recipe lock" — two writers in the critical section.

Keeping the sentinel file alive means every opener always opens the **same**
inode.  The file is cheap (0 bytes of content) and creates itself on first
use, so there is no operational penalty to leaving it in place.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import structlog

from recotem.config import get_lock_dir

logger = structlog.get_logger(__name__)

# Process-wide set of remote lock paths for which the WARN-level advisory has
# already been emitted.  Subsequent calls for the same path use DEBUG so
# repeated CronJob invocations don't spam the log.
_warned_remote_paths: set[str] = set()


class LockContestedError(Exception):
    """Raised when the recipe lock is held by another process and
    ``fail_on_busy=True`` was requested."""

    code = "lock_contested"


class LockTimeoutError(LockContestedError):
    """Raised when the recipe lock could not be acquired within the timeout.

    This is a subclass of ``LockContestedError`` so existing
    ``except LockContestedError`` handlers continue to work without change.

    Attributes
    ----------
    waited_seconds:
        Approximate wall-clock seconds spent waiting for the lock before
        giving up.  Useful for distinguishing "timed out after waiting" from
        "immediately unavailable" in operational logs.
    """

    code = "lock_timeout"

    def __init__(self, message: str, *, waited_seconds: float) -> None:
        super().__init__(message)
        self.waited_seconds = waited_seconds


_LOCAL_SCHEMES = {"", "file"}


def _remote_lock_path(output_str: str) -> Path:
    """Derive a host-local lock-file path for a remote-scheme output URI.

    ``Path("s3://bucket/key.recotem.lock")`` resolves to a relative path
    rooted at the current working directory, which fails under Helm's
    ``readOnlyRootFilesystem: true``. Map remote URIs to a stable path
    under ``$RECOTEM_LOCK_DIR`` (preferred) or the system temp dir.
    """
    base_env = get_lock_dir()
    base = Path(base_env) if base_env else Path(tempfile.gettempdir()) / "recotem-locks"
    digest = hashlib.sha256(output_str.encode("utf-8")).hexdigest()
    return base / f"{digest}.lock"


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
        Only when *fail_on_busy* is ``True`` and the lock cannot be acquired
        immediately (i.e. ``timeout=0`` or first-attempt failure).
    LockTimeoutError
        Subclass of ``LockContestedError``.  Raised when *timeout* > 0 and
        the deadline expires before the lock is acquired.  Carries
        ``waited_seconds`` for operational log correlation.
    """
    output_str = str(output_path)
    scheme = urlparse(output_str).scheme.lower() if "://" in output_str else ""
    if scheme not in _LOCAL_SCHEMES:
        # ``flock`` is a host-local primitive. For remote outputs derive a
        # stable lock path under a writable host-local dir (Helm's root fs
        # is read-only; ``Path("s3://...lock")`` would resolve under cwd
        # and fail). The lock still cannot coordinate writers across hosts
        # — surface that via the structured warning so operators don't
        # assume distributed mutual exclusion. See
        # docs/operations.md "Concurrent training" section.
        lock_path = _remote_lock_path(output_str)
        _lock_path_str = str(lock_path)
        _log_kwargs: dict[str, str] = {
            "scheme": scheme,
            "output_path": output_str,
            "lock_path": _lock_path_str,
            "advice": (
                "per-recipe flock is host-local; ensure single-writer via the "
                "scheduler (CronJob concurrencyPolicy=Forbid, Argo mutex, etc.)"
            ),
        }
        if _lock_path_str not in _warned_remote_paths:
            _warned_remote_paths.add(_lock_path_str)
            logger.warning("recipe_lock_local_only", **_log_kwargs)
        else:
            logger.debug("recipe_lock_local_only", **_log_kwargs)
    else:
        lock_path = Path(output_str + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        win_result = _try_acquire_windows(lock_path)
        if win_result is None:
            if fail_on_busy:
                raise LockContestedError(
                    f"Recipe lock at {lock_path} is held by another process."
                )
            yield False
            return
        # win_result is the open fd; keep it open across the yield so the
        # msvcrt lock is held.  Close in finally to release the lock.
        try:
            yield True
        finally:
            import msvcrt  # noqa: PLC0415 (Windows only)

            try:
                msvcrt.locking(win_result, msvcrt.LK_UNLCK, 1)
            except OSError as _unlock_exc:
                # Unlocking can fail (e.g. ENOTLOCK if the fd was already
                # released by a foreign process, or if the file disappeared).
                # Always log the failure so operators investigating a stuck
                # train can correlate "next train blocked" with the unlock
                # error rather than chase a phantom contention bug.
                logger.warning(
                    "recipe_lock_windows_unlock_failed",
                    lock_path=str(lock_path),
                    errno=_unlock_exc.errno,
                    error=str(_unlock_exc),
                )
            os.close(win_result)
        return

    # POSIX path via fcntl.flock
    import fcntl  # noqa: PLC0415 (POSIX only)

    lock_op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if timeout == 0.0:
        lock_op |= fcntl.LOCK_NB  # non-blocking

    _O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY | _O_NOFOLLOW, 0o600)  # noqa: S103 – mode is 0o600 (owner-only); CodeQL false positive (py/world-readable-file)
    except OSError as _open_exc:
        # ELOOP: O_NOFOLLOW detected a symlink at the lock path — tampered
        # sentinel; refuse to acquire and emit a structured warning, then
        # propagate the OSError.  The caller gets a clear signal that this is
        # a security anomaly, not ordinary lock contention.
        if _open_exc.errno == errno.ELOOP:
            logger.warning(
                "recipe_lock_unsafe_symlink",
                lock_path=str(lock_path),
                advice=(
                    "Lock path is a symlink — potential symlink-swap attack. "
                    "Remove the symlink and retry."
                ),
            )
            raise
        # EACCES / EPERM: lock directory or sentinel has wrong permissions.
        # Treat as "lock not acquireable" — same semantics as contention.
        if _open_exc.errno in (errno.EACCES, errno.EPERM):
            if fail_on_busy:
                raise LockContestedError(
                    f"Recipe lock at {lock_path} is not accessible: {_open_exc}"
                ) from _open_exc
            yield False
            return
        # Any other OSError (ENOSPC, ENAMETOOLONG, EIO, …) is a genuine system
        # problem — propagate so the caller can map to _EXIT_UNKNOWN.
        raise
    try:
        try:
            if timeout > 0:
                # Polling loop for a timed acquire.
                import time  # noqa: PLC0415

                start = time.monotonic()
                deadline = start + timeout
                while True:
                    try:
                        fcntl.flock(fd, lock_op | fcntl.LOCK_NB)
                        break
                    except OSError as _poll_exc:
                        # Only retry on genuine lock-contention errno values.
                        # EBADF, ENOLCK, EIO, etc. indicate a real error and
                        # must not be silently swallowed as "try again later".
                        if _poll_exc.errno not in (
                            errno.EWOULDBLOCK,
                            errno.EACCES,
                            errno.EAGAIN,
                        ):
                            raise
                        now = time.monotonic()
                        if now >= deadline:
                            waited = now - start
                            logger.warning(
                                "recipe_lock_timeout",
                                lock_path=str(lock_path),
                                waited_seconds=round(waited, 3),
                                timeout=timeout,
                            )
                            raise LockTimeoutError(
                                f"Recipe lock at {lock_path} could not be acquired "
                                f"within {timeout}s (waited {waited:.3f}s).",
                                waited_seconds=waited,
                            ) from _poll_exc
                        time.sleep(0.05)
            else:
                fcntl.flock(fd, lock_op)
        except OSError as exc:
            # Only treat genuine lock-contention errno values as "busy".
            # EBADF, ENOLCK, EIO, etc. indicate a real system problem and
            # must not be silently converted to "lock contested".
            if exc.errno not in (errno.EWOULDBLOCK, errno.EACCES, errno.EAGAIN):
                raise
            if fail_on_busy:
                raise LockContestedError(
                    f"Recipe lock at {lock_path} is held by another process. "
                    "Pass --fail-on-busy to surface this as an error, or wait."
                ) from exc
            yield False
            return

        # The sentinel file is left on disk intentionally — see module docstring.
        yield True

    finally:
        # os.close(fd) releases the flock automatically on POSIX; an explicit
        # LOCK_UN call before close is redundant and opens an error window if
        # the fd has already been invalidated.
        os.close(fd)


def _try_acquire_windows(lock_path: Path) -> int | None:
    """Acquire a per-recipe lock on Windows using msvcrt.locking.

    Opens (or creates) the sentinel file and calls ``msvcrt.LK_NBLCK`` on
    the first byte to take an exclusive lock.  The sentinel file is
    intentionally **never deleted** — see the module docstring for the
    inode-rotation race rationale.

    Returns the open fd (int) when the lock is acquired, or ``None`` when
    another process holds it.  The caller must keep the fd open across its
    critical section and close it (releasing the lock) in a ``finally``.

    Note: ``msvcrt.locking`` is host-local and process-scoped.  It does not
    coordinate writers across machines — use a scheduler-level mutex
    (e.g. Windows Scheduled Task with ``–ExecutionTimeLimit``) for that.
    """
    import msvcrt  # noqa: PLC0415 (Windows only)

    try:
        fd = os.open(
            str(lock_path),
            os.O_CREAT | os.O_WRONLY,
            0o600,  # noqa: S103 – mode is 0o600 (owner-only); CodeQL false positive (py/world-readable-file)
        )
    except OSError:
        return None
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        # Another process holds the lock byte.
        os.close(fd)
        return None
    return fd
