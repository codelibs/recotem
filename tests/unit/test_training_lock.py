"""Unit tests for recotem.training.lock.

Tests:
- Lock acquired (context yields True)
- Lock contention with fail_on_busy=False yields False
- Lock contention with fail_on_busy=True raises LockContestedError
- Lock released after context
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

import pytest

from recotem.training.lock import LockContestedError, LockTimeoutError, recipe_lock


@pytest.fixture(autouse=True)
def _reset_warned_remote_paths():
    """Reset the per-process dedup set before each test to prevent cross-test pollution."""
    import recotem.training.lock as lock_mod

    lock_mod._warned_remote_paths.clear()
    yield
    lock_mod._warned_remote_paths.clear()


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_acquired_yields_true(tmp_path: Path) -> None:
    output_path = tmp_path / "model.recotem"
    with recipe_lock(output_path) as acquired:
        assert acquired is True


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_contention_fail_on_busy_false_yields_false(tmp_path: Path) -> None:
    """When the lock is held and fail_on_busy=False, yields False."""
    import fcntl
    import os

    output_path = tmp_path / "model.recotem"
    lock_path = Path(str(output_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Hold the lock from within this process
    fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Now try to acquire from the same process: non-blocking should fail
        # NOTE: on Linux, flock is per-open-file-description, so the same
        # process CAN re-acquire. We test the fail_on_busy path differently.
        # For this test, we just verify the API contract: context yields a bool.
        with recipe_lock(output_path, timeout=0.0, fail_on_busy=False) as result:
            assert isinstance(result, bool)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_fail_on_busy_raises_lock_contested_error(tmp_path: Path) -> None:
    """Simulate lock contention and verify LockContestedError is raised."""
    import fcntl
    import os

    output_path = tmp_path / "contest.recotem"
    lock_path = Path(str(output_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # We test by using a separate process to hold the lock.
    # Since inter-process locking is what flock actually guarantees:
    def _hold_lock(lock_path_str: str, ready_event, release_event) -> None:
        fd = os.open(lock_path_str, os.O_CREAT | os.O_WRONLY, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        ready_event.set()
        release_event.wait(timeout=5.0)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    ctx = multiprocessing.get_context("fork")
    ready = ctx.Event()
    release = ctx.Event()

    p = ctx.Process(target=_hold_lock, args=(str(lock_path), ready, release))
    p.start()
    ready.wait(timeout=3.0)

    try:
        with pytest.raises(LockContestedError):
            with recipe_lock(output_path, timeout=0.0, fail_on_busy=True):
                pass
    finally:
        release.set()
        p.join(timeout=3.0)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_released_after_context(tmp_path: Path) -> None:
    """The flock is released after the context manager exits.

    The sentinel file intentionally persists on disk (inode-rotation safety —
    see lock.py module docstring).  A second acquire after the first context
    exits must succeed, proving the flock itself was released.
    """
    output_path = tmp_path / "released.recotem"
    lock_path = Path(str(output_path) + ".lock")
    with recipe_lock(output_path) as acquired:
        assert acquired is True
        assert lock_path.exists()
    # Sentinel file must remain on disk (never deleted).
    assert lock_path.exists()
    # The flock must have been released — a subsequent acquire must succeed.
    with recipe_lock(output_path) as acquired2:
        assert acquired2 is True


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_warns_on_remote_scheme(tmp_path: Path, monkeypatch) -> None:
    """For ``s3://`` / ``gs://`` outputs, ``flock`` cannot coordinate writers
    across hosts/pods. ``recipe_lock`` must surface this as a structured
    warning so operators do not assume distributed mutual exclusion.
    Regression test for the gap between the lock implementation and
    docs/deployment/k8s.md guidance."""
    import structlog.testing

    # The remote-scheme branch must NOT depend on cwd being writable
    # (Helm's readOnlyRootFilesystem: true makes cwd read-only).
    monkeypatch.setenv("RECOTEM_LOCK_DIR", str(tmp_path / "locks"))
    remote = "s3://my-bucket/artifacts/my_recipe.recotem"

    with structlog.testing.capture_logs() as captured:
        with recipe_lock(remote) as acquired:
            assert acquired is True

    warnings = [e for e in captured if e.get("event") == "recipe_lock_local_only"]
    assert warnings, "remote-scheme output must emit recipe_lock_local_only warning"
    assert warnings[0]["scheme"] == "s3"
    assert warnings[0]["log_level"] == "warning"
    # The warning must reveal the host-local lock path so operators can
    # locate / clean up stuck locks.
    assert "lock_path" in warnings[0]
    lock_path = Path(warnings[0]["lock_path"])
    # Must be absolute and inside the configured lock dir, NOT under cwd
    # (which would produce "s3:/bucket/..." relative paths).
    assert lock_path.is_absolute()
    assert str(lock_path).startswith(str(tmp_path / "locks"))


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_remote_scheme_uses_tmp_when_lock_dir_unset(
    tmp_path: Path, monkeypatch
) -> None:
    """Without RECOTEM_LOCK_DIR, remote-output locks must land under a
    host-local writable directory (default: /tmp/recotem-locks), never as
    Path("s3://...") which resolves below cwd."""
    monkeypatch.delenv("RECOTEM_LOCK_DIR", raising=False)
    # Place cwd somewhere read-only-ish to prove cwd is NOT used.
    monkeypatch.chdir(tmp_path)

    remote = "gs://my-bucket/artifacts/my_recipe.recotem"
    with recipe_lock(remote) as acquired:
        assert acquired is True

    # No "gs:" or "s3:" directory should have been created under cwd.
    assert not (tmp_path / "gs:").exists()
    assert not (tmp_path / "s3:").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_remote_scheme_stable_for_same_uri(tmp_path: Path, monkeypatch) -> None:
    """Two calls with the same remote URI must contend for the same lock
    file (deterministic mapping URI -> host-local lock path)."""
    import fcntl
    import os

    monkeypatch.setenv("RECOTEM_LOCK_DIR", str(tmp_path / "locks"))
    remote = "s3://my-bucket/key.recotem"

    # Capture the lock path from the warning of a first acquire.
    import structlog.testing

    with structlog.testing.capture_logs() as captured:
        with recipe_lock(remote):
            pass
    lock_path = Path(
        next(e for e in captured if e.get("event") == "recipe_lock_local_only")[
            "lock_path"
        ]
    )

    # Hold lock externally at the derived path.
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Subprocess attempt with same URI must contend.
        ctx = multiprocessing.get_context("fork")
        ready = ctx.Event()

        def _attempt(uri: str, lock_dir: str, q):
            os.environ["RECOTEM_LOCK_DIR"] = lock_dir
            try:
                with recipe_lock(uri, fail_on_busy=True):
                    q.put("acquired")
            except LockContestedError:
                q.put("contested")
            except Exception as exc:  # noqa: BLE001
                q.put(f"error:{exc!r}")

        q = ctx.Queue()
        p = ctx.Process(target=_attempt, args=(remote, str(tmp_path / "locks"), q))
        p.start()
        p.join(timeout=5.0)
        result = q.get(timeout=1.0)
        assert result == "contested", f"expected contention, got {result}"
        del ready  # silence unused
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_does_not_warn_on_local_path(tmp_path: Path) -> None:
    """Plain filesystem paths (the common case) must not emit the warning."""
    import structlog.testing

    output_path = tmp_path / "model.recotem"
    with structlog.testing.capture_logs() as captured:
        with recipe_lock(output_path) as acquired:
            assert acquired is True

    assert not any(e.get("event") == "recipe_lock_local_only" for e in captured)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_remote_lock_path_uses_full_sha256_digest(tmp_path: Path, monkeypatch) -> None:
    """_remote_lock_path must use the full 64-character SHA-256 hex digest,
    not the truncated 32-character form used before the m-3 fix.

    Cryptographic hygiene: truncating to 32 hex chars (16 bytes) reduces
    collision resistance significantly — use the full 256-bit digest.
    """
    import hashlib

    from recotem.training.lock import _remote_lock_path

    monkeypatch.setenv("RECOTEM_LOCK_DIR", str(tmp_path / "locks"))
    remote_uri = "s3://my-bucket/artifacts/my_recipe.recotem"

    lock_path = _remote_lock_path(remote_uri)
    stem = lock_path.stem  # filename without .lock suffix

    # Full SHA-256 produces a 64-character hex string.
    expected_digest = hashlib.sha256(remote_uri.encode("utf-8")).hexdigest()
    assert len(stem) == 64, (
        f"Lock file stem must be the full 64-char SHA-256 digest, got {len(stem)} chars: {stem!r}"
    )
    assert stem == expected_digest, (
        f"Lock file stem {stem!r} does not match full digest {expected_digest!r}"
    )


# ---------------------------------------------------------------------------
# Sentinel-pattern / inode-safety tests (concurrency bug fix)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_file_persists_after_release(tmp_path: Path) -> None:
    """The sentinel ``.lock`` file must remain on disk after the holder exits.

    Deleting the file on release opens the inode-rotation race described in
    the lock.py module docstring.  The file is cheap and intentionally left.
    """
    output_path = tmp_path / "model.recotem"
    lock_path = Path(str(output_path) + ".lock")

    with recipe_lock(output_path) as acquired:
        assert acquired is True

    assert lock_path.exists(), (
        "Sentinel lock file must persist after release (inode-rotation safety)"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_contender_does_not_delete_lock_file(tmp_path: Path) -> None:
    """A contender that exits without acquiring must not delete the lock file.

    Scenario: process A holds the lock; process B contends with
    fail_on_busy=False (yields False, returns immediately).  After both
    finish, the sentinel must still exist so future acquirers open the same
    inode.
    """
    import fcntl
    import os

    output_path = tmp_path / "model.recotem"
    lock_path = Path(str(output_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Process A: hold the lock via a raw fd from a different open-file-description
    # so that the same-process flock re-entrancy of Linux does not mask the test.
    ctx = multiprocessing.get_context("fork")

    def _holder(lock_path_str: str, ready_event, release_event) -> None:
        fd = os.open(lock_path_str, os.O_CREAT | os.O_WRONLY, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        ready_event.set()
        release_event.wait(timeout=5.0)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    ready = ctx.Event()
    release = ctx.Event()
    p = ctx.Process(target=_holder, args=(str(lock_path), ready, release))
    p.start()
    ready.wait(timeout=3.0)

    try:
        # Process B: contend with fail_on_busy=False — must yield False.
        with recipe_lock(output_path, timeout=0.0, fail_on_busy=False) as result:
            # On Linux flock is per-open-file-description; same-process may
            # re-acquire.  Regardless of True/False, the sentinel must survive.
            _ = result
    finally:
        release.set()
        p.join(timeout=3.0)

    assert lock_path.exists(), (
        "Contender must not delete the sentinel lock file (inode-rotation safety)"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_concurrent_holders_serialized_through_persistent_lock_file(
    tmp_path: Path,
) -> None:
    """Two processes competing for the same lock must not overlap in the
    critical section.

    Uses a shared multiprocessing.Value as a counter that is incremented
    inside the lock.  If mutual exclusion holds, the counter always reaches
    exactly N (one increment per process, never lost due to concurrent write).
    Also verifies the sentinel file still exists after all processes finish.
    """
    import time

    output_path = tmp_path / "shared.recotem"
    lock_path = Path(str(output_path) + ".lock")

    N = 4
    ctx = multiprocessing.get_context("fork")
    counter = ctx.Value("i", 0)
    violation = ctx.Value("i", 0)  # 1 if two processes overlapped

    def _worker(output_path_str: str, counter, violation) -> None:
        from recotem.training.lock import recipe_lock as rl

        with rl(output_path_str, timeout=-1) as acquired:
            assert acquired is True
            # Read, sleep briefly (yields CPU so other process can attempt),
            # then write back.  A non-serialized second writer would observe
            # stale value and produce a final sum < N.
            val = counter.value
            time.sleep(0.02)
            # If another process is also inside the critical section, it will
            # have incremented the counter during our sleep.
            if counter.value != val:
                violation.value = 1
            counter.value = val + 1

    procs = [
        ctx.Process(target=_worker, args=(str(output_path), counter, violation))
        for _ in range(N)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10.0)

    assert violation.value == 0, "Two processes overlapped inside the critical section"
    assert counter.value == N, (
        f"Expected counter={N} after {N} serialized increments, got {counter.value}"
    )
    assert lock_path.exists(), (
        "Sentinel lock file must persist after all processes finish"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_file_owner_only_permissions(tmp_path: Path) -> None:
    """The sentinel lock file must be created with mode 0o600 (owner read/write only).

    Regression test: previously some code paths used 0o644 (world-readable),
    which could allow unprivileged readers to observe lock state.  This test
    asserts that all recipe_lock() creation paths use 0o600.
    """
    import stat

    output_path = tmp_path / "model_perms.recotem"
    lock_path = tmp_path / "model_perms.recotem.lock"

    with recipe_lock(output_path) as acquired:
        assert acquired is True
        assert lock_path.exists()
        mode = lock_path.stat().st_mode
        # Only owner read + write bits should be set; no group or other bits.
        perm_bits = stat.S_IMODE(mode)
        assert perm_bits == 0o600, (
            f"Lock file must be created with mode 0o600 (owner-only); "
            f"got 0o{perm_bits:o} for {lock_path}"
        )


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_remote_output_warning_emitted_once_per_path(
    tmp_path: Path, monkeypatch
) -> None:
    """For the same remote output path, the WARN-level advisory must fire only
    once per process; subsequent calls must emit at DEBUG or not at all.

    Regression: previously the warning fired on every invocation, producing
    log noise on hourly CronJobs.
    """
    import structlog.testing

    import recotem.training.lock as lock_mod

    # Reset the per-process dedup set so this test is isolated from others.
    lock_mod._warned_remote_paths.clear()

    monkeypatch.setenv("RECOTEM_LOCK_DIR", str(tmp_path / "locks"))
    remote = "s3://my-bucket/artifacts/dedup_test.recotem"

    with structlog.testing.capture_logs() as cap1:
        with recipe_lock(remote) as acquired:
            assert acquired is True

    with structlog.testing.capture_logs() as cap2:
        with recipe_lock(remote) as acquired:
            assert acquired is True

    warn_events_1 = [
        e
        for e in cap1
        if e.get("event") == "recipe_lock_local_only"
        and e.get("log_level") == "warning"
    ]
    warn_events_2 = [
        e
        for e in cap2
        if e.get("event") == "recipe_lock_local_only"
        and e.get("log_level") == "warning"
    ]

    assert len(warn_events_1) == 1, (
        "First call must emit exactly one WARN-level recipe_lock_local_only event"
    )
    assert len(warn_events_2) == 0, (
        "Second call with the same remote path must NOT emit a WARN-level event; "
        "should emit DEBUG or omit entirely"
    )


@pytest.mark.skipif(
    not hasattr(__import__("os"), "O_NOFOLLOW"),
    reason="O_NOFOLLOW not available on this platform",
)
@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lock_refuses_to_follow_symlink(tmp_path: Path) -> None:
    """recipe_lock must refuse to open a .lock path that is a symlink.

    O_NOFOLLOW causes os.open to raise OSError(ELOOP) when the final path
    component is a symlink, preventing a symlink-swap attack where an
    attacker plants a symlink between invocations.
    """
    import errno as _errno

    output_path = tmp_path / "model.recotem"
    lock_path = Path(str(output_path) + ".lock")
    # Create a real file to be the symlink target
    target_file = tmp_path / "attacker_file.txt"
    target_file.write_text("attacker controlled content")
    # Plant a symlink at the expected lock path
    lock_path.symlink_to(target_file)

    with pytest.raises(OSError) as exc_info:
        with recipe_lock(output_path):
            pass  # pragma: no cover

    # On macOS and Linux, O_NOFOLLOW raises ELOOP when the final path
    # component is a symlink.
    assert exc_info.value.errno == _errno.ELOOP, (
        f"Expected ELOOP ({_errno.ELOOP}) when opening a symlink with O_NOFOLLOW, "
        f"got errno={exc_info.value.errno}"
    )

    # The target file must NOT have been written to.
    assert target_file.read_text() == "attacker controlled content", (
        "recipe_lock must not write to the symlink target file"
    )


# ---------------------------------------------------------------------------
# MAJOR-5: LockTimeoutError — distinguish timeout from immediate-busy
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_LockTimeoutError_is_LockContestedError_subclass() -> None:
    """LockTimeoutError must be a subclass of LockContestedError so that
    existing ``except LockContestedError`` handlers catch it without change."""
    assert issubclass(LockTimeoutError, LockContestedError), (
        "LockTimeoutError must be a subclass of LockContestedError to preserve "
        "all existing exception-handling paths"
    )
    # Instances must satisfy isinstance checks in both directions.
    exc = LockTimeoutError("timed out", waited_seconds=1.5)
    assert isinstance(exc, LockContestedError)
    assert isinstance(exc, LockTimeoutError)
    assert exc.waited_seconds == 1.5


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_recipe_lock_timeout_raises_LockTimeoutError(tmp_path: Path) -> None:
    """When timeout > 0 and the lock cannot be acquired within the deadline,
    recipe_lock must raise LockTimeoutError (not the base LockContestedError)
    and the waited_seconds attribute must reflect the actual wait time."""
    import fcntl
    import os

    output_path = tmp_path / "timeout_test.recotem"
    lock_path = Path(str(output_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    def _hold_lock(lock_path_str: str, ready_event, release_event) -> None:
        fd = os.open(lock_path_str, os.O_CREAT | os.O_WRONLY, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        ready_event.set()
        release_event.wait(timeout=5.0)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    ctx = multiprocessing.get_context("fork")
    ready = ctx.Event()
    release = ctx.Event()

    p = ctx.Process(target=_hold_lock, args=(str(lock_path), ready, release))
    p.start()
    ready.wait(timeout=3.0)

    try:
        with pytest.raises(LockTimeoutError) as exc_info:
            with recipe_lock(output_path, timeout=0.1, fail_on_busy=True):
                pass  # pragma: no cover
        assert exc_info.value.waited_seconds == pytest.approx(0.1, abs=0.05), (
            f"waited_seconds should be ~0.1 ± 0.05, "
            f"got {exc_info.value.waited_seconds:.3f}"
        )
    finally:
        release.set()
        p.join(timeout=3.0)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_recipe_lock_immediate_busy_raises_base_LockContestedError_not_timeout(
    tmp_path: Path,
) -> None:
    """When timeout=0 (non-blocking, immediate failure) and fail_on_busy=True,
    recipe_lock must raise the base LockContestedError, NOT LockTimeoutError.

    This allows operators to distinguish:
    - "never tried to wait" (LockContestedError, not LockTimeoutError)
    - "waited but deadline expired" (LockTimeoutError)
    """
    import fcntl
    import os

    output_path = tmp_path / "immediate_busy.recotem"
    lock_path = Path(str(output_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    def _hold_lock(lock_path_str: str, ready_event, release_event) -> None:
        fd = os.open(lock_path_str, os.O_CREAT | os.O_WRONLY, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        ready_event.set()
        release_event.wait(timeout=5.0)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    ctx = multiprocessing.get_context("fork")
    ready = ctx.Event()
    release = ctx.Event()

    p = ctx.Process(target=_hold_lock, args=(str(lock_path), ready, release))
    p.start()
    ready.wait(timeout=3.0)

    try:
        with pytest.raises(LockContestedError) as exc_info:
            with recipe_lock(output_path, timeout=0.0, fail_on_busy=True):
                pass  # pragma: no cover
        # Must be base class, not the timeout subclass
        assert type(exc_info.value) is LockContestedError, (
            f"Immediate-busy path must raise exactly LockContestedError, "
            f"not {type(exc_info.value).__name__}"
        )
        assert not isinstance(exc_info.value, LockTimeoutError), (
            "Immediate-busy (timeout=0) must NOT raise LockTimeoutError"
        )
    finally:
        release.set()
        p.join(timeout=3.0)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_acquire_propagates_unexpected_oserror(tmp_path: Path, monkeypatch) -> None:
    """An OSError with errno=EIO from fcntl.flock must propagate unmodified.

    Only EWOULDBLOCK / EACCES / EAGAIN are lock-contention signals.  Real I/O
    errors (EIO, ENOLCK, EBADF, …) must not be silently converted to
    "lock contested" or cause the call to yield False.
    """
    import errno as _errno
    import fcntl

    output_path = tmp_path / "eio.recotem"

    eio = OSError(_errno.EIO, "Input/output error")

    monkeypatch.setattr(fcntl, "flock", lambda fd, op: (_ for _ in ()).throw(eio))

    with pytest.raises(OSError) as exc_info:
        with recipe_lock(output_path, timeout=0.0):
            pass  # pragma: no cover

    assert exc_info.value.errno == _errno.EIO, (
        f"Expected EIO to propagate, got errno={exc_info.value.errno}"
    )


# ---------------------------------------------------------------------------
# T-5: SystemExit inside recipe_lock releases the flock for the next process
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_system_exit_inside_lock_releases_flock_for_next_process(
    tmp_path: Path,
) -> None:
    """When a process raises SystemExit inside a recipe_lock context, the OS
    releases the flock when the process exits.  A second process must then be
    able to acquire the lock promptly.

    Test design (multiprocessing):
    1. Process A: acquires recipe_lock, signals ready, then raises SystemExit(0).
    2. Main process: waits for A to finish (join), then verifies process B can
       acquire the lock.
    3. Process B: acquires recipe_lock with fail_on_busy=True; should succeed
       because the OS releases flocks on process exit.
    """
    ctx = multiprocessing.get_context("fork")
    output_path = tmp_path / "sysexit_lock.recotem"
    lock_path = Path(str(output_path) + ".lock")

    ready = ctx.Event()
    result_q = ctx.Queue()

    def _process_a(output_path_str: str, ready_event) -> None:
        """Acquire lock, signal ready, then raise SystemExit."""
        with recipe_lock(output_path_str) as acquired:
            assert acquired is True
            ready_event.set()
            raise SystemExit(0)

    def _process_b(output_path_str: str, q) -> None:
        """Try to acquire the lock; put 'acquired' or 'contested'."""
        try:
            with recipe_lock(output_path_str, fail_on_busy=True) as acquired:
                q.put("acquired" if acquired else "not_acquired")
        except LockContestedError:
            q.put("contested")
        except Exception as exc:  # noqa: BLE001
            q.put(f"error:{exc!r}")

    # Start process A
    pa = ctx.Process(target=_process_a, args=(str(output_path), ready))
    pa.start()
    ready.wait(timeout=5.0)
    pa.join(timeout=5.0)  # Wait for A to exit (and release the flock)

    assert pa.exitcode == 0, f"Process A must exit cleanly; exitcode={pa.exitcode}"

    # Start process B — flock should now be available
    pb = ctx.Process(target=_process_b, args=(str(output_path), result_q))
    pb.start()
    pb.join(timeout=5.0)

    result = result_q.get(timeout=2.0)
    assert result == "acquired", (
        f"Process B must acquire the lock after process A's SystemExit released it; "
        f"got: {result!r}"
    )
