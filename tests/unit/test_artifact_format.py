"""Unit tests for recotem.artifact.format (Section 11 artifact/ list).

Tests structural parsing, magic checks, version enforcement, size caps,
and reserved-bytes guard.
NOTE: This test module builds raw .recotem artifact bytes for security testing.
The pickle usage here is tested under HMAC protection: we test that the format
layer rejects malformed bytes BEFORE any deserialization occurs.
"""

from __future__ import annotations

import struct

import pytest

from recotem.artifact.format import (
    FORMAT_VERSION,
    MAGIC,
    MAX_HEADER_LEN,
    ArtifactError,
    ArtifactHeader,
    build_artifact_bytes,
    parse_header_from_bytes,
)
from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact


def _valid_artifact(
    kid: str = "active",
    key_hex: str = ACTIVE_KEY_HEX,
    header_dict: dict | None = None,
) -> bytes:
    return build_raw_artifact(kid=kid, key_hex=key_hex, header_dict=header_dict)


def test_parse_valid_artifact_returns_header() -> None:
    data = _valid_artifact()
    hdr = parse_header_from_bytes(data, max_payload_bytes=2**31)
    assert isinstance(hdr, ArtifactHeader)
    assert hdr.version == FORMAT_VERSION
    assert hdr.kid == "active"
    assert len(hdr.hmac_digest) == 32


def test_magic_bytes_wrong_rejected_before_hmac_work() -> None:
    data = bytearray(_valid_artifact())
    data[0] = ord("X")
    with pytest.raises(ArtifactError, match="magic"):
        parse_header_from_bytes(bytes(data), max_payload_bytes=2**31)


def test_truncated_before_hmac_rejected() -> None:
    data = _valid_artifact()
    truncated = data[:14]
    with pytest.raises(ArtifactError):
        parse_header_from_bytes(truncated, max_payload_bytes=2**31)


def test_truncated_mid_payload_parses_ok_or_fails_gracefully() -> None:
    """Truncated payload does not crash with unhandled exception."""
    data = _valid_artifact()
    truncated = data[:-5]
    try:
        hdr = parse_header_from_bytes(truncated, max_payload_bytes=2**31)
        assert hdr is not None
    except ArtifactError:
        pass


def test_format_version_zero_rejected() -> None:
    data = bytearray(_valid_artifact())
    struct.pack_into("<H", data, 8, 0)
    with pytest.raises(ArtifactError, match="version"):
        parse_header_from_bytes(bytes(data), max_payload_bytes=2**31)


def test_format_version_unsupported_future_rejected() -> None:
    data = bytearray(_valid_artifact())
    struct.pack_into("<H", data, 8, FORMAT_VERSION + 1)
    with pytest.raises(ArtifactError, match="version"):
        parse_header_from_bytes(bytes(data), max_payload_bytes=2**31)


def test_reserved_bytes_nonzero_rejected() -> None:
    data = bytearray(_valid_artifact())
    struct.pack_into("<H", data, 10, 1)
    with pytest.raises(ArtifactError, match="reserved"):
        parse_header_from_bytes(bytes(data), max_payload_bytes=2**31)


def test_header_length_exceeding_64KiB_rejected() -> None:
    data = _valid_artifact()
    kid_len = data[12]
    hmac_offset = 13 + kid_len
    header_len_offset = hmac_offset + 32
    data_ba = bytearray(data)
    struct.pack_into("<I", data_ba, header_len_offset, MAX_HEADER_LEN + 1)
    with pytest.raises(ArtifactError, match="header_len"):
        parse_header_from_bytes(bytes(data_ba), max_payload_bytes=2**31)


def test_header_not_valid_utf8_rejected() -> None:
    import hmac as _hmac

    payload = b"\x80\x04\x95\x09\x00\x00\x00\x00\x00\x00\x00}\x8c\x01x\x8c\x01y\x86."
    header_json = b"\xff\xfe invalid utf8"
    kid = "active"
    kid_bytes = kid.encode("utf-8")
    key_bytes = bytes.fromhex(ACTIVE_KEY_HEX)
    h = _hmac.new(key_bytes, digestmod="sha256")
    h.update(kid_bytes)
    h.update(header_json)
    h.update(payload)
    digest = h.digest()
    parts = [
        MAGIC,
        struct.pack("<HH", FORMAT_VERSION, 0),
        bytes([len(kid_bytes)]),
        kid_bytes,
        digest,
        struct.pack("<I", len(header_json)),
        header_json,
        payload,
    ]
    data = b"".join(parts)
    with pytest.raises(ArtifactError, match="UTF-8"):
        parse_header_from_bytes(data, max_payload_bytes=2**31)


def test_payload_size_exceeding_max_bytes_rejected() -> None:
    data = _valid_artifact()
    with pytest.raises(ArtifactError, match="exceeds cap"):
        parse_header_from_bytes(data, max_payload_bytes=10)


def test_build_artifact_bytes_roundtrip() -> None:
    import hmac as _hmac

    kid = "kid1"
    key = bytes.fromhex(ACTIVE_KEY_HEX)
    payload = b"\x80\x04\x95\x04\x00\x00\x00\x00\x00\x00\x00}\x8c\x01a\x8c\x01b\x86."
    header_json = b'{"x":1}'
    kid_bytes = kid.encode("utf-8")
    h = _hmac.new(key, digestmod="sha256")
    h.update(kid_bytes)
    h.update(header_json)
    h.update(payload)
    digest = h.digest()
    data = build_artifact_bytes(kid, digest, header_json, payload)
    hdr = parse_header_from_bytes(data, max_payload_bytes=2**31)
    assert hdr.kid == kid
    assert hdr.header_data == header_json
    assert data[hdr.payload_offset :] == payload


def test_build_artifact_bytes_kid_too_long_rejected() -> None:
    with pytest.raises(ArtifactError, match="kid_len"):
        build_artifact_bytes("x" * 33, b"\x00" * 32, b"{}", b"")


def test_build_artifact_bytes_kid_empty_rejected() -> None:
    with pytest.raises(ArtifactError, match="kid_len"):
        build_artifact_bytes("", b"\x00" * 32, b"{}", b"")
