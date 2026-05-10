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
