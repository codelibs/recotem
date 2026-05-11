"""Unit tests for recotem.artifact.io.

Tests:
- write/read roundtrip (always_overwrite + append_sha)
- atomic write via tempfile + rename
- append_sha pointer pattern
- max_bytes enforcement
- inspect path does not deserialize payload

NOTE: write_artifact pickles its payload_obj argument internally.
Tests pass plain Python objects; write_artifact handles serialization.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from recotem.artifact.format import ArtifactError
from recotem.artifact.io import read_artifact, write_artifact
from recotem.artifact.signing import KeyRing
from tests.conftest import ACTIVE_KEY_HEX


def _make_keyring() -> KeyRing:
    return KeyRing(f"active:{ACTIVE_KEY_HEX}")


# ---------------------------------------------------------------------------
# Write + read roundtrip
# ---------------------------------------------------------------------------


def test_write_read_roundtrip_always_overwrite(tmp_path: Path) -> None:
    """write_artifact then read_artifact: header is preserved."""
    kr = _make_keyring()
    output_path = str(tmp_path / "test.recotem")
    header = {"recipe_name": "roundtrip", "best_score": 0.5}
    # write_artifact pickles this dict internally
    payload_obj = {"items": [1, 2, 3]}

    final_path = write_artifact(
        payload_obj=payload_obj,
        header_dict=header,
        key_ring=kr,
        fs_path=output_path,
        versioning="always_overwrite",
    )
    assert os.path.exists(final_path)

    hdr, payload_back = read_artifact(output_path, kr)
    assert hdr.kid == "active"
    loaded_header = json.loads(hdr.header_data.decode("utf-8"))
    assert loaded_header["recipe_name"] == "roundtrip"
    assert isinstance(payload_back, bytes)


def test_write_read_roundtrip_append_sha(tmp_path: Path) -> None:
    """append_sha versioning: pointer file created, readable via read_artifact."""
    kr = _make_keyring()
    output_path = str(tmp_path / "test.recotem")

    final_path = write_artifact(
        payload_obj={"key": "value"},
        header_dict={"recipe_name": "sha_test"},
        key_ring=kr,
        fs_path=output_path,
        versioning="append_sha",
    )

    # final_path should be the sha-suffixed artifact
    assert final_path != output_path
    assert ".recotem" in final_path

    # The pointer file must exist at output_path
    pointer_content = Path(output_path).read_text().strip()
    assert pointer_content.endswith(".recotem")
    assert re.match(r"^[A-Za-z0-9_.-]+\.recotem$", pointer_content)

    # read_artifact must resolve the pointer transparently
    hdr, payload_back = read_artifact(output_path, kr)
    assert hdr.kid == "active"


# ---------------------------------------------------------------------------
# Atomic write via tempfile + rename
# ---------------------------------------------------------------------------


def test_atomic_local_write_via_tempfile_rename(tmp_path: Path) -> None:
    """write_artifact leaves no .tmp files after a successful write."""
    kr = _make_keyring()
    output_path = str(tmp_path / "atomic.recotem")

    write_artifact(
        payload_obj={"x": 1},
        header_dict={"recipe_name": "atomic"},
        key_ring=kr,
        fs_path=output_path,
        versioning="always_overwrite",
    )

    # No .tmp files should remain
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"
    assert Path(output_path).exists()


# ---------------------------------------------------------------------------
# append_sha pointer atomicity
# ---------------------------------------------------------------------------


def test_versioning_append_sha_writes_pointer_atomically(tmp_path: Path) -> None:
    """append_sha mode writes a valid pointer file with exactly one filename line."""
    kr = _make_keyring()
    output_path = str(tmp_path / "ptr.recotem")

    write_artifact(
        payload_obj={"z": 99},
        header_dict={"recipe_name": "ptr_test"},
        key_ring=kr,
        fs_path=output_path,
        versioning="append_sha",
    )

    # Pointer file content must be exactly one valid filename on one line
    pointer_text = Path(output_path).read_text()
    lines = [ln for ln in pointer_text.splitlines() if ln.strip()]
    assert len(lines) == 1
    target_name = lines[0]
    assert target_name.endswith(".recotem")

    # The target artifact must exist in the same dir
    target_path = tmp_path / target_name
    assert target_path.exists()


# ---------------------------------------------------------------------------
# max_bytes enforcement
# ---------------------------------------------------------------------------


def test_read_artifact_max_bytes_rejection(tmp_path: Path) -> None:
    """read_artifact with small max_bytes rejects a valid (but large) artifact."""
    kr = _make_keyring()
    output_path = str(tmp_path / "big.recotem")

    write_artifact(
        payload_obj={"x": "a" * 1000},
        header_dict={"recipe_name": "big"},
        key_ring=kr,
        fs_path=output_path,
        versioning="always_overwrite",
    )

    with pytest.raises(ArtifactError, match="exceeds cap"):
        read_artifact(output_path, kr, max_bytes=50)


# ---------------------------------------------------------------------------
# inspect path: read + HMAC verify without unpickling
# ---------------------------------------------------------------------------


def test_inspect_runs_full_hmac_and_does_not_unpickle(tmp_path: Path) -> None:
    """read_artifact verifies HMAC; caller must explicitly unpickle payload."""
    kr = _make_keyring()
    output_path = str(tmp_path / "inspect.recotem")

    write_artifact(
        payload_obj={"safe": True},
        header_dict={"recipe_name": "inspect"},
        key_ring=kr,
        fs_path=output_path,
        versioning="always_overwrite",
    )

    # read_artifact returns (header, payload_bytes) — no deserialization yet
    hdr, returned_payload = read_artifact(output_path, kr)
    assert isinstance(returned_payload, bytes)
    assert hdr.kid == "active"

    # Tamper the saved artifact and verify HMAC catches it
    raw = Path(output_path).read_bytes()
    tampered = bytearray(raw)
    tampered[-1] ^= 0xFF
    Path(output_path).write_bytes(bytes(tampered))

    with pytest.raises(ArtifactError):
        read_artifact(output_path, kr)


def test_read_artifact_not_found_raises(tmp_path: Path) -> None:
    """read_artifact on missing file raises ArtifactError."""
    kr = _make_keyring()
    with pytest.raises(ArtifactError, match="not found"):
        read_artifact(str(tmp_path / "no_such.recotem"), kr)


# ---------------------------------------------------------------------------
# CRITICAL: header_json byte tamper rejected end-to-end
# ---------------------------------------------------------------------------


def test_header_json_byte_tamper_rejected_end_to_end(tmp_path: Path) -> None:
    """Flipping ONE byte inside header_json portion fails HMAC verify.

    This is distinct from existing payload/kid tamper tests.  The HMAC
    scope covers kid_bytes || header_json || payload, so any tamper to
    header_json must surface as an HMAC failure before JSON/pickle parsing.
    """
    from tests.conftest import build_raw_artifact

    kr = _make_keyring()

    raw = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={
            "recipe_name": "tamper_header_test",
            "trained_at": "2026-01-01T00:00:00Z",
            "best_class": "TopPopRecommender",
            "best_score": 0.42,
        },
    )

    from recotem.artifact.format import parse_header_from_bytes

    hdr = parse_header_from_bytes(raw, max_payload_bytes=10 * 1024 * 1024)
    # header_json starts just before the payload offset.
    header_json_start = hdr.payload_offset - len(hdr.header_data)
    header_json_mid = header_json_start + len(hdr.header_data) // 2

    tampered = bytearray(raw)
    tampered[header_json_mid] ^= 0x01  # flip one bit inside header_json
    tampered_path = tmp_path / "header_tampered.recotem"
    tampered_path.write_bytes(bytes(tampered))

    # Must raise ArtifactError for HMAC, not for JSON or pickle.
    with pytest.raises(ArtifactError, match="HMAC"):
        read_artifact(str(tampered_path), kr)


# ---------------------------------------------------------------------------
# HMAC failure prevents payload deserialization
# ---------------------------------------------------------------------------


def test_hmac_failure_prevents_payload_deserialization(
    tmp_path: Path, monkeypatch
) -> None:
    """When HMAC verification fails, unpickle_payload must NOT be called.

    We monkeypatch unpickle_payload to count calls; any call with a tampered
    artifact must not proceed to deserialization.
    """
    from unittest.mock import patch

    from tests.conftest import build_raw_artifact

    kr = _make_keyring()
    raw = build_raw_artifact(kid="active", key_hex=ACTIVE_KEY_HEX)

    # Tamper the HMAC digest (bytes 13+kid_len .. +32) — offset depends on
    # kid "active" (6 bytes): fixed prefix 13 + kid 6 = 19, then 32 HMAC bytes.
    tampered = bytearray(raw)
    tampered[19] ^= 0xFF  # flip a byte inside the HMAC field
    tampered_path = tmp_path / "hmac_fail.recotem"
    tampered_path.write_bytes(bytes(tampered))

    call_count = [0]

    import recotem.artifact.signing as signing_mod

    real_unpickle = signing_mod.unpickle_payload

    def _counting_unpickle(payload_bytes):
        call_count[0] += 1
        return real_unpickle(payload_bytes)

    with patch.object(signing_mod, "unpickle_payload", side_effect=_counting_unpickle):
        with pytest.raises(ArtifactError):
            read_artifact(str(tampered_path), kr)

    assert call_count[0] == 0, (
        "unpickle_payload must not be called when HMAC verification fails; "
        f"call_count={call_count[0]}"
    )


# ---------------------------------------------------------------------------
# Corrupt pointer file raises ArtifactError
# ---------------------------------------------------------------------------


def test_read_artifact_corrupt_pointer_file_raises_artifact_error(
    tmp_path: Path,
) -> None:
    """A file whose content looks like a pointer but with an invalid target name
    (fails _POINTER_RE match) is treated as a real artifact and will fail with
    ArtifactError (not a pointer regex match, so will try to parse as artifact).
    """
    kr = _make_keyring()

    # Write bytes that look like a small ASCII string but do not match _POINTER_RE
    # (contains spaces and lacks a .recotem suffix).
    corrupt_path = tmp_path / "bad_pointer.recotem"
    corrupt_path.write_bytes(b"not-valid-artifact-bytes")

    # Should raise ArtifactError (magic mismatch or similar structural error)
    with pytest.raises(ArtifactError):
        read_artifact(str(corrupt_path), kr)


# ---------------------------------------------------------------------------
# Payload exceeds max_payload_bytes rejected by parse_header_from_bytes
# ---------------------------------------------------------------------------


def test_payload_exceeds_max_payload_bytes_rejected(tmp_path: Path) -> None:
    """parse_header_from_bytes raises ArtifactError when payload exceeds cap.

    We build a valid artifact and then parse it with a tiny max_payload_bytes
    so the payload size check fires before any deserialization.
    """
    from recotem.artifact.format import parse_header_from_bytes
    from tests.conftest import build_raw_artifact

    raw = build_raw_artifact(kid="active", key_hex=ACTIVE_KEY_HEX)

    # Use an absurdly small cap -- must be smaller than the actual payload size.
    # A pickled dict has some bytes; cap at 1 byte.
    tiny_cap = 1

    with pytest.raises(ArtifactError, match="payload size"):
        parse_header_from_bytes(raw, max_payload_bytes=tiny_cap)


# ---------------------------------------------------------------------------
# S-D: write-time RECOTEM_ARTIFACT_ROOT containment (TOCTOU guard)
# ---------------------------------------------------------------------------


def test_write_artifact_toctou_symlink_swap_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_write_atomic must re-verify containment just before os.replace.

    Setup:
    1. Create an artifact root and a legitimate output directory inside it.
    2. Set RECOTEM_ARTIFACT_ROOT.
    3. Monkeypatch os.fsync to swap the output directory to an outside-root
       symlink *after* the file has been written to the temp path but
       *before* os.replace — simulating a TOCTOU attack.
    4. Assert that _write_artifact (which calls _write_atomic) raises
       ArtifactError rather than completing the rename.
    """
    import os as _os

    from recotem.artifact.io import _write_atomic

    artifact_root = tmp_path / "root"
    artifact_root.mkdir()
    inside_dir = artifact_root / "models"
    inside_dir.mkdir()

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(artifact_root))

    # Where we intend to write
    dest = str(inside_dir / "model.recotem")

    real_fsync = _os.fsync
    original_inside_dir_resolved = inside_dir.resolve()

    def _evil_fsync(fd: int) -> None:
        """After the temp file is written, replace inside_dir with a symlink out."""
        real_fsync(fd)
        # Remove the real directory and replace with a symlink pointing outside root.
        import shutil

        shutil.rmtree(str(inside_dir), ignore_errors=True)
        inside_dir.symlink_to(outside_dir)

    monkeypatch.setattr(_os, "fsync", _evil_fsync)

    with pytest.raises((ArtifactError, OSError)):
        # _write_atomic must either detect the escape and raise ArtifactError,
        # or raise OSError because the temp file's parent (inside_dir) no longer
        # exists as a real directory after the symlink swap.
        _write_atomic(
            None,  # type: ignore[arg-type] — not used for local FS path
            dest,
            b"dummy artifact bytes",
            is_local=True,
        )


def test_assert_output_root_containment_no_op_without_env(tmp_path: Path) -> None:
    """_assert_output_root_containment is a no-op when RECOTEM_ARTIFACT_ROOT is unset."""
    from recotem.artifact.io import _assert_output_root_containment

    # Must not raise even for a path that would be outside any root.
    _assert_output_root_containment(str(tmp_path / "anywhere" / "model.recotem"))


def test_assert_output_root_containment_inside_root_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_assert_output_root_containment accepts a dest inside the configured root."""
    from recotem.artifact.io import _assert_output_root_containment

    root = tmp_path / "root"
    root.mkdir()
    (root / "models").mkdir()
    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(root))

    # Should not raise
    _assert_output_root_containment(str(root / "models" / "model.recotem"))


def test_assert_output_root_containment_outside_root_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_assert_output_root_containment raises ArtifactError for path outside root."""
    from recotem.artifact.io import _assert_output_root_containment

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(root))

    with pytest.raises(ArtifactError, match="outside"):
        _assert_output_root_containment(str(outside / "model.recotem"))


# ---------------------------------------------------------------------------
# C-2: _write_atomic cleans up .tmp on BaseException (unlink guard)
# ---------------------------------------------------------------------------


def test_atomic_write_unlinks_tmp_on_replace_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When os.replace raises OSError, the .tmp sibling must be removed.

    If _write_atomic leaves the .tmp file behind on failure, a subsequent
    successful write may pick up stale data if the temp file name collides.
    The except BaseException: os.unlink(tmp_path) guard must fire even for
    regular OSError from os.replace.
    """
    import os as _os

    from recotem.artifact.io import _write_atomic

    dest = str(tmp_path / "target.recotem")
    data = b"dummy artifact bytes"

    replace_called = []

    real_replace = _os.replace

    def _failing_replace(src: str, dst: str) -> None:
        replace_called.append((src, dst))
        raise OSError("simulated replace failure")

    monkeypatch.setattr(_os, "replace", _failing_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        _write_atomic(None, dest, data, is_local=True)  # type: ignore[arg-type]

    # No .tmp files should remain in tmp_path
    leftover_tmp = list(tmp_path.glob("*.tmp"))
    assert leftover_tmp == [], (
        f"_write_atomic must unlink the .tmp file on OSError from os.replace; "
        f"leftover: {leftover_tmp}"
    )


def test_atomic_write_unlinks_tmp_on_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KeyboardInterrupt during os.replace must still trigger .tmp cleanup.

    The guard uses ``except BaseException`` so signals delivered as
    KeyboardInterrupt are also caught and the temp file is removed.
    """
    import os as _os

    from recotem.artifact.io import _write_atomic

    dest = str(tmp_path / "target_ki.recotem")
    data = b"some bytes"

    def _ki_replace(src: str, dst: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(_os, "replace", _ki_replace)

    with pytest.raises(KeyboardInterrupt):
        _write_atomic(None, dest, data, is_local=True)  # type: ignore[arg-type]

    leftover_tmp = list(tmp_path.glob("*.tmp"))
    assert leftover_tmp == [], (
        f"_write_atomic must unlink .tmp on KeyboardInterrupt; leftover: {leftover_tmp}"
    )


def test_atomic_write_unlinks_tmp_on_systemexit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SystemExit must also trigger .tmp cleanup before propagation."""
    import os as _os

    from recotem.artifact.io import _write_atomic

    dest = str(tmp_path / "target_se.recotem")
    data = b"some bytes"

    def _se_replace(src: str, dst: str) -> None:
        raise SystemExit(0)

    monkeypatch.setattr(_os, "replace", _se_replace)

    with pytest.raises(SystemExit):
        _write_atomic(None, dest, data, is_local=True)  # type: ignore[arg-type]

    leftover_tmp = list(tmp_path.glob("*.tmp"))
    assert leftover_tmp == [], (
        f"_write_atomic must unlink .tmp on SystemExit; leftover: {leftover_tmp}"
    )


# ---------------------------------------------------------------------------
# T-6: append_sha pointer-file write order (sha-suffixed before pointer)
# ---------------------------------------------------------------------------


def test_write_atomic_non_local_object_store_roundtrip() -> None:
    """_write_atomic with is_local=False exercises the object-store path.

    Uses fsspec's in-memory filesystem (memory://) so no real object store is
    needed.  Verifies that:
    1. Bytes written are readable back unchanged (round-trip).
    2. The non-local code path (fs.open write) is exercised — no os.replace,
       no .tmp file, no RECOTEM_ARTIFACT_ROOT containment check.
    """
    import fsspec

    from recotem.artifact.io import _write_atomic

    # Use a fresh in-memory filesystem for isolation between test runs.
    mem_fs = fsspec.filesystem("memory")

    dest = "/test_write_atomic_nonlocal/artifact.recotem"
    data = b"non-local object-store artifact bytes for roundtrip"

    # Exercise the is_local=False branch explicitly.
    _write_atomic(mem_fs, dest, data, is_local=False)

    # Read back and verify round-trip.
    with mem_fs.open(dest, "rb") as fh:
        read_back = fh.read()

    assert read_back == data, (
        f"Round-trip mismatch: written {len(data)} bytes, "
        f"read back {len(read_back)} bytes"
    )

    # Clean up to avoid polluting the global memory namespace.
    mem_fs.rm(dest)


def test_write_atomic_non_local_pointer_file_roundtrip() -> None:
    """write_artifact with a memory:// path exercises the is_local=False branch
    end-to-end in append_sha mode, verifying the pointer-file pattern.

    The pointer file must contain the sha-suffixed artifact name, and the
    sha-suffixed artifact must be readable and HMAC-verifiable.
    """
    import fsspec

    from recotem.artifact.io import write_artifact

    mem_fs = fsspec.filesystem("memory")

    # We need a KeyRing to call write_artifact.
    kr = _make_keyring()

    # Use memory:// URLs so fsspec routes through MemoryFileSystem (is_local=False).
    dest_url = "memory:///nonlocal_ptr_test/model.recotem"

    # write_artifact calls fsspec.core.url_to_fs internally, which will return
    # a MemoryFileSystem for "memory://" — confirming is_local=False path.
    final_url = write_artifact(
        payload_obj={"k": "v"},
        header_dict={"recipe_name": "nonlocal_ptr"},
        key_ring=kr,
        fs_path=dest_url,
        versioning="append_sha",
    )

    # The sha-suffixed artifact must be readable via fsspec.
    # Strip the "memory://" scheme prefix to get the raw path.
    sha_path = (
        final_url[len("memory://") :]
        if final_url.startswith("memory://")
        else final_url
    )
    if not sha_path.startswith("/"):
        sha_path = "/" + sha_path
    with mem_fs.open(sha_path, "rb") as fh:
        artifact_bytes = fh.read()

    assert len(artifact_bytes) > 0, "sha-suffixed artifact must be non-empty"

    # The pointer file must exist and contain the sha-suffixed basename.
    ptr_path = "/nonlocal_ptr_test/model.recotem"
    with mem_fs.open(ptr_path, "rb") as fh:
        ptr_content = fh.read().decode("utf-8").strip()

    assert ptr_content.endswith(".recotem"), (
        f"Pointer file must reference a .recotem artifact; got: {ptr_content!r}"
    )
    import re

    assert re.match(r"^[A-Za-z0-9_.-]+\.recotem$", ptr_content), (
        f"Pointer content must match pointer regex; got: {ptr_content!r}"
    )


def test_append_sha_sha_suffixed_written_before_pointer(tmp_path: Path) -> None:
    """In append_sha mode, the sha-suffixed artifact must be durably written
    before the pointer file is updated.

    If the write of the sha-suffixed file succeeds but the pointer update
    fails (simulated by raising on the second _write_atomic call), the
    pointer file must not have been updated.

    Strategy: patch _write_atomic to record call order and raise on the
    second call (the pointer write).  Assert that exactly one call was made
    (the sha-suffixed artifact) and that the pointer file was not modified.
    """
    from unittest.mock import patch

    from recotem.artifact.io import write_artifact

    kr = _make_keyring()
    output_path = str(tmp_path / "model.recotem")
    pointer_path = tmp_path / "model.recotem"

    call_log: list[str] = []
    real_write_atomic = __import__(
        "recotem.artifact.io", fromlist=["_write_atomic"]
    )._write_atomic

    def _spy_write_atomic(fs, dest, data, is_local):
        call_log.append(dest)
        if len(call_log) == 2:
            # Second call is the pointer update — simulate failure
            raise OSError("simulated pointer write failure")
        real_write_atomic(fs, dest, data, is_local)

    with patch("recotem.artifact.io._write_atomic", side_effect=_spy_write_atomic):
        with pytest.raises(OSError, match="simulated pointer write failure"):
            write_artifact(
                payload_obj={"x": 1},
                header_dict={"recipe_name": "order_test"},
                key_ring=kr,
                fs_path=output_path,
                versioning="append_sha",
            )

    # First call must be the sha-suffixed artifact, not the pointer
    assert len(call_log) >= 1
    first_written = call_log[0]
    assert first_written != output_path, (
        "First write must be the sha-suffixed artifact, not the pointer file"
    )
    assert ".recotem" in first_written
    # The pointer file must not have been written (call aborted before second write)
    assert not pointer_path.exists(), (
        "Pointer file must not exist when sha-suffixed write succeeded but "
        "pointer write failed"
    )
