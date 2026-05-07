"""Binary container layout for .recotem artifact files.

Defines constants, dataclasses, and the ArtifactError exception used by
artifact/io.py and artifact/signing.py.

Layout (all integers little-endian):

    Offset  Size  Field
    ------  ----  -----
    0       8     Magic bytes: b"RECOTEM\\0"
    8       2     Format version (uint16 LE); must be FORMAT_VERSION (1)
    10      2     Reserved (uint16 LE); must be 0
    12      1     Key-id length K (uint8); 1 ≤ K ≤ 32
    13      K     Key-id bytes (UTF-8)
    13+K    32    HMAC-SHA256 digest
    45+K    4     Header JSON length N (uint32 LE); N ≤ 65536
    49+K    N     Header JSON (UTF-8)
    49+K+N  M     Pickle payload
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAGIC: bytes = b"RECOTEM\x00"
FORMAT_VERSION: int = 1

MAGIC_SIZE: int = 8
VERSION_SIZE: int = 2
RESERVED_SIZE: int = 2
KID_LEN_SIZE: int = 1
HMAC_SIZE: int = 32
HEADER_LEN_SIZE: int = 4

# Fixed-size prefix before variable-length kid bytes
FIXED_PREFIX_SIZE: int = MAGIC_SIZE + VERSION_SIZE + RESERVED_SIZE + KID_LEN_SIZE
# = 13

MAX_KID_LEN: int = 32
MIN_KID_LEN: int = 1
MAX_HEADER_LEN: int = 65_536
DEFAULT_MAX_PAYLOAD_BYTES: int = 2 * 1024 * 1024 * 1024  # 2 GiB

# Struct format strings (little-endian)
_FMT_VERSION_RESERVED = "<HH"  # 2 × uint16 LE
_FMT_HEADER_LEN = "<I"  # uint32 LE


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ArtifactError(Exception):
    """Raised for any structural, security, or integrity failure in an artifact."""


# ---------------------------------------------------------------------------
# Parsed header dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactHeader:
    """Parsed, validated view of the fixed-layout fields of a .recotem file.

    ``header_data`` holds the raw UTF-8 bytes of the embedded JSON blob; the
    caller is responsible for ``json.loads``-ing it.  Keeping it as bytes
    here avoids double-parsing and keeps this dataclass dependency-free.
    """

    version: int
    kid: str
    hmac_digest: bytes  # 32 bytes, read from file; not yet verified here
    header_data: bytes  # raw Header JSON UTF-8 bytes
    payload_offset: int  # byte offset at which the payload begins


# ---------------------------------------------------------------------------
# Low-level binary parsing helpers
# ---------------------------------------------------------------------------


def parse_header_from_bytes(data: bytes, max_payload_bytes: int) -> ArtifactHeader:
    """Parse and validate the fixed-layout prefix of *data*.

    Raises ``ArtifactError`` on any structural violation before any allocation
    beyond what is strictly necessary to read the fixed fields.

    Does *not* verify the HMAC — that is the responsibility of
    ``artifact.signing``.

    Parameters
    ----------
    data:
        The complete artifact bytes (already read into memory).
    max_payload_bytes:
        Upper bound on the payload size in bytes.  The payload size is
        inferred from ``len(data) - payload_offset``; if it exceeds this cap
        the function raises before returning.
    """
    offset = 0

    # 1. Magic
    if len(data) < MAGIC_SIZE:
        raise ArtifactError("artifact too short: missing magic bytes")
    if data[:MAGIC_SIZE] != MAGIC:
        raise ArtifactError(
            f"magic bytes mismatch: expected {MAGIC!r}, got {data[:MAGIC_SIZE]!r}"
        )
    offset += MAGIC_SIZE

    # 2. Version + Reserved (4 bytes total)
    if len(data) < offset + VERSION_SIZE + RESERVED_SIZE:
        raise ArtifactError("artifact too short: missing version/reserved fields")
    version, reserved = struct.unpack_from(_FMT_VERSION_RESERVED, data, offset)
    offset += VERSION_SIZE + RESERVED_SIZE

    if version == 0 or version > FORMAT_VERSION:
        raise ArtifactError(
            f"unsupported format version {version}; "
            f"this build supports up to version {FORMAT_VERSION}"
        )
    if reserved != 0:
        raise ArtifactError(f"reserved bytes must be 0, got {reserved!r}")

    # 3. kid_len
    if len(data) < offset + KID_LEN_SIZE:
        raise ArtifactError("artifact too short: missing kid_len byte")
    kid_len: int = data[offset]
    offset += KID_LEN_SIZE

    if kid_len < MIN_KID_LEN or kid_len > MAX_KID_LEN:
        raise ArtifactError(
            f"kid_len {kid_len} out of range [{MIN_KID_LEN}, {MAX_KID_LEN}]"
        )

    # 4. kid bytes
    if len(data) < offset + kid_len:
        raise ArtifactError("artifact too short: truncated kid bytes")
    kid_bytes = data[offset : offset + kid_len]
    try:
        kid = kid_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactError(f"kid is not valid UTF-8: {exc}") from exc
    offset += kid_len

    # 5. HMAC digest (32 bytes)
    if len(data) < offset + HMAC_SIZE:
        raise ArtifactError("artifact too short: truncated HMAC field")
    hmac_digest = data[offset : offset + HMAC_SIZE]
    offset += HMAC_SIZE

    # 6. Header JSON length (uint32 LE) — reject before allocation
    if len(data) < offset + HEADER_LEN_SIZE:
        raise ArtifactError("artifact too short: missing header_len field")
    (header_len,) = struct.unpack_from(_FMT_HEADER_LEN, data, offset)
    offset += HEADER_LEN_SIZE

    if header_len > MAX_HEADER_LEN:
        raise ArtifactError(
            f"header_len {header_len} exceeds maximum {MAX_HEADER_LEN}; "
            "refusing allocation"
        )

    # 7. Header JSON bytes
    if len(data) < offset + header_len:
        raise ArtifactError("artifact too short: truncated header JSON")
    header_data = data[offset : offset + header_len]
    try:
        header_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactError(f"header JSON is not valid UTF-8: {exc}") from exc
    offset += header_len

    # 8. Payload size cap (checked *after* full read, but before returning)
    payload_size = len(data) - offset
    if payload_size > max_payload_bytes:
        raise ArtifactError(
            f"payload size {payload_size} exceeds cap {max_payload_bytes}; "
            "refusing to load"
        )

    return ArtifactHeader(
        version=version,
        kid=kid,
        hmac_digest=hmac_digest,
        header_data=header_data,
        payload_offset=offset,
    )


def build_artifact_bytes(
    kid: str,
    hmac_digest: bytes,
    header_json: bytes,
    payload: bytes,
) -> bytes:
    """Assemble the complete artifact byte string from its components.

    Used by ``artifact.io.write_artifact``.  All structural invariants
    (MAGIC, FORMAT_VERSION, etc.) are applied here so callers cannot omit
    them.
    """
    kid_bytes = kid.encode("utf-8")
    kid_len = len(kid_bytes)
    if kid_len < MIN_KID_LEN or kid_len > MAX_KID_LEN:
        raise ArtifactError(
            f"kid_len {kid_len} out of range [{MIN_KID_LEN}, {MAX_KID_LEN}]"
        )

    header_len = len(header_json)
    if header_len > MAX_HEADER_LEN:
        raise ArtifactError(
            f"header_json length {header_len} exceeds maximum {MAX_HEADER_LEN}"
        )

    parts: list[bytes] = [
        MAGIC,
        struct.pack(_FMT_VERSION_RESERVED, FORMAT_VERSION, 0),  # version + reserved
        bytes([kid_len]),
        kid_bytes,
        hmac_digest,
        struct.pack(_FMT_HEADER_LEN, header_len),
        header_json,
        payload,
    ]
    return b"".join(parts)
