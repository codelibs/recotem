"""Per-recipe file locking.

Provides ``recipe_lock(path, ...)`` which returns a context manager that
acquires an exclusive file lock at ``<path>.lock``.

Implements the spec's lock semantics (Section 6 step 2):
- Default: exclusive mode (LOCK_EX).
- If lock is contended (non-blocking acquire fails): yield False so the
  caller can exit 0 gracefully (default), or raise ``LockContestedError``
  when ``fail_on_busy=True``.
- If the lock path is not writable (EACCES/EPERM, or EROFS for a read-only
  filesystem): raise ``LockPermissionError`` — always, regardless of
  ``fail_on_busy``.
  Contention is transient and skipping is the right answer; a permission
  failure is a deployment mistake that no retry fixes, and skipping it would
  exit 0 without training. See ``LockPermissionError``.
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

from recotem.config import ConfigError, get_lock_dir

logger = structlog.get_logger(__name__)

# Process-wide set of remote lock paths for which the WARN-level advisory has
# already been emitted.  Subsequent calls for the same path use DEBUG so
# repeated CronJob invocations don't spam the log.
_warned_remote_paths: set[str] = set()

# errno values meaning "this path cannot be written, and no amount of waiting
# will change that": wrong ownership/mode (EACCES/EPERM) or a read-only
# filesystem (EROFS — a read-only bind mount, a read-only PVC, or Helm's
# ``readOnlyRootFilesystem: true``).  Matched on the errno rather than caught
# as ``PermissionError`` because EROFS raises a plain ``OSError``: catching the
# exception type alone lets a read-only mount escape to _EXIT_UNKNOWN.
_UNWRITABLE_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.EROFS})


class LockContestedError(Exception):
    """Raised when the recipe lock is held by another process and
    ``fail_on_busy=True`` was requested."""

    code = "lock_contested"


class LockPermissionError(ConfigError):
    """Raised when the recipe lock cannot be created or opened for lack of
    filesystem permission.

    Deliberately **not** a ``LockContestedError``.  Contention means "another
    process holds the lock right now" — a transient condition whose correct
    response is to skip this run and let the next scheduled one succeed.  A
    permission failure is a deployment mistake (wrong volume ownership, a
    read-only mount, a mistyped ``RECOTEM_LOCK_DIR``): it will not clear on
    retry, and skipping makes ``recotem train`` exit 0 without training, so a
    cron/CronJob reports success while the model silently goes stale.  That is
    the hardest possible failure to notice, which is exactly why it must not
    share a code path — or an exit code — with contention.

    Subclassing ``ConfigError`` maps this to exit 8 (configuration error) via
    the existing ``_map_exception_to_exit`` branch, keeping exit 6
    (``_EXIT_LOCK_CONTESTED``) meaning "retry later" for schedulers that
    branch on it.
    """

    code = "lock_permission_denied"


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


def _lock_permission_error(lock_path: Path, exc: OSError) -> LockPermissionError:
    """Log and build the ``LockPermissionError`` for an unwritable lock path.

    Centralised so every call site emits the same structured event and the
    same operator advice.  The message names the exact path that could not be
    written and what to change, because the operator reading it is looking at
    a scheduler log, not a shell.

    The remedy is split by errno: chmod/chown fixes an ownership problem but
    is useless against EROFS, where the only way out is a writable location.
    """
    identity = ""
    if hasattr(os, "getuid"):  # POSIX only; keeps the helper importable anywhere
        identity = f" (running as uid={os.getuid()}, gid={os.getgid()})"

    if exc.errno == errno.EROFS:
        cause = "the filesystem is mounted read-only"
        remedy = (
            "Point RECOTEM_LOCK_DIR at a writable directory (an emptyDir or "
            "PVC mount under readOnlyRootFilesystem: true), or write "
            "output.path to a writable filesystem."
        )
    else:
        cause = "the path is not writable by this user"
        remedy = (
            "Make the containing directory writable by the user running "
            "recotem, or set RECOTEM_LOCK_DIR to a writable directory."
        )

    logger.error(
        "recipe_lock_permission_denied",
        lock_path=str(lock_path),
        errno=exc.errno,
        error=str(exc),
        advice=(
            f"Lock path is not writable: {cause}. This is a configuration "
            "error, not lock contention — training was NOT skipped, it "
            f"failed. {remedy}"
        ),
    )
    return LockPermissionError(
        f"Cannot create or open the recipe lock at {lock_path}: "
        f"{exc.strerror or exc}{identity}. {cause[0].upper()}{cause[1:]}, so "
        "this is a configuration error rather than lock contention: retrying "
        "will not help, and the run was failed rather than skipped. "
        f"{remedy}"
    )


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
    LockPermissionError
        The lock directory or sentinel is not writable.  Raised regardless of
        *fail_on_busy* — this is a configuration error, not contention, so it
        never yields ``False``.
    """
    # Defence-in-depth: the CLI validates lock_timeout before calling this
    # function, but library callers may pass an arbitrary float.  Values < 0
    # other than -1.0 have no defined meaning (−1 = indefinite wait, 0 =
    # non-blocking, positive = timed wait); catch them early so callers get a
    # clear AssertionError rather than silent indefinite-wait behaviour.
    assert timeout == -1.0 or timeout >= 0, (
        f"lock timeout must be -1 (indefinite), 0 (non-blocking), or a "
        f"positive number of seconds; got {timeout!r}"
    )

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
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as _mkdir_exc:
        # Same misconfiguration as the unwritable-path open below, one step
        # earlier: a mistyped RECOTEM_LOCK_DIR or a read-only mount can make
        # the lock *directory* uncreatable. Route it through the same error so
        # the operator gets the path and the remedy instead of a bare
        # traceback mapped to _EXIT_UNKNOWN.  Any other OSError (ENOSPC,
        # ENAMETOOLONG, ENOTDIR, …) is a genuine system problem and keeps
        # propagating.
        if _mkdir_exc.errno not in _UNWRITABLE_ERRNOS:
            raise
        raise _lock_permission_error(lock_path, _mkdir_exc) from _mkdir_exc

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
        # EACCES / EPERM / EROFS: the lock directory or sentinel has wrong
        # permissions, or its filesystem is mounted read-only.  This is NOT
        # contention: flock() is advisory, so a lock already held by another
        # process never makes os.open() fail — the contended case is detected
        # below, at the flock() call.  Reaching here means the process
        # genuinely cannot write the sentinel, which no amount of retrying
        # fixes.  Raise regardless of `fail_on_busy`: yielding False here used
        # to make `recotem train` exit 0 without training, so a scheduled run
        # reported success while the model went stale.  See
        # LockPermissionError for the full rationale.
        if _open_exc.errno in _UNWRITABLE_ERRNOS:
            raise _lock_permission_error(lock_path, _open_exc) from _open_exc
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
                # Only reachable when the caller already passed --fail-on-busy,
                # so the message must tell them what to do next, not recommend
                # the flag they are already using.
                raise LockContestedError(
                    f"Recipe lock at {lock_path} is held by another process. "
                    "Retry once that run finishes, pass --lock-timeout to wait "
                    "for it, or drop --fail-on-busy to skip this run instead."
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
    except OSError as _open_exc:
        # EPERM and EROFS are never sharing violations, so they mean the same
        # thing here as on POSIX: the sentinel is not writable. Fail loudly.
        if _open_exc.errno in (errno.EPERM, errno.EROFS):
            raise _lock_permission_error(lock_path, _open_exc) from _open_exc
        # EACCES / EAGAIN: on Windows EACCES is ambiguous — it is both
        # "permission denied" and the sharing-violation errno — so unlike the
        # POSIX branch this cannot be classified from the errno alone, and is
        # still treated as "lock contested" (return None) to avoid failing
        # genuine contention on a platform this project does not test on. It
        # is logged at WARNING so the case is at least visible in the operator
        # log rather than an unexplained exit 0.
        # All other errno values indicate a genuine system problem (ENOENT,
        # ENOSPC, …) that must propagate so the operator sees the real root cause.
        if _open_exc.errno in (errno.EACCES, errno.EAGAIN):
            logger.warning(
                "recipe_lock_windows_open_denied",
                lock_path=str(lock_path),
                errno=_open_exc.errno,
                error=str(_open_exc),
                advice=(
                    "Treating as lock contention, but on Windows this errno "
                    "is also 'permission denied'. If no other recotem process "
                    "is running, check the permissions on this path."
                ),
            )
            return None
        logger.warning(
            "recipe_lock_windows_open_failed",
            lock_path=str(lock_path),
            errno=_open_exc.errno,
            error=str(_open_exc),
        )
        raise
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        # Another process holds the lock byte.
        os.close(fd)
        return None
    return fd
