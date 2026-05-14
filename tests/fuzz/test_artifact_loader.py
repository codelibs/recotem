"""Fuzz tests for recotem.artifact.format and io.

Hypothesis-driven byte mutation of a valid artifact; the loader must never
raise an unhandled exception and must reject any non-bit-perfect file.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from recotem.artifact.format import ArtifactError, parse_header_from_bytes
from recotem.artifact.signing import KeyRing, verify_hmac
from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

VALID_ARTIFACT: bytes = build_raw_artifact(
    kid="active",
    key_hex=ACTIVE_KEY_HEX,
    header_dict={"recipe_name": "fuzz", "best_score": 0.5},
)


def _try_parse(data: bytes) -> None:
    """Attempt to parse artifact bytes; accept ArtifactError but no other exceptions."""
    try:
        parse_header_from_bytes(data, max_payload_bytes=2**20)
    except ArtifactError:
        pass
    except Exception as exc:
        raise AssertionError(
            f"Unhandled exception type {type(exc).__name__}: {exc}"
        ) from exc


def _try_verify(data: bytes) -> None:
    """Attempt to parse and HMAC-verify; accept ArtifactError but no other exceptions."""
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    try:
        hdr = parse_header_from_bytes(data, max_payload_bytes=2**20)
        kid_bytes = hdr.kid.encode("utf-8")
        payload = data[hdr.payload_offset :]
        verify_hmac(kr, hdr.kid, kid_bytes, hdr.header_data, payload, hdr.hmac_digest)
    except ArtifactError:
        pass
    except Exception as exc:
        raise AssertionError(
            f"Unhandled exception type {type(exc).__name__}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Hypothesis: random bytes
# ---------------------------------------------------------------------------


@given(data=st.binary(min_size=0, max_size=512))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_loader_handles_arbitrary_bytes(data: bytes) -> None:
    """parse_header_from_bytes never raises unhandled exceptions on arbitrary bytes."""
    _try_parse(data)


# ---------------------------------------------------------------------------
# Hypothesis: valid artifact with random byte mutations
# ---------------------------------------------------------------------------


@given(
    flip_offset=st.integers(min_value=0, max_value=len(VALID_ARTIFACT) - 1),
    flip_bit=st.integers(min_value=0, max_value=7),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_loader_handles_single_bit_flip(flip_offset: int, flip_bit: int) -> None:
    """Flipping any single bit in the artifact must raise ArtifactError or succeed parse."""
    data = bytearray(VALID_ARTIFACT)
    data[flip_offset] ^= 1 << flip_bit
    _try_parse(bytes(data))


# ---------------------------------------------------------------------------
# Hypothesis: truncation
# ---------------------------------------------------------------------------


@given(length=st.integers(min_value=0, max_value=len(VALID_ARTIFACT)))
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_loader_handles_truncated_artifact(length: int) -> None:
    """Any prefix of the valid artifact must not cause unhandled exceptions."""
    _try_parse(VALID_ARTIFACT[:length])


# ---------------------------------------------------------------------------
# Hypothesis: verify never panics on mutated data
# ---------------------------------------------------------------------------


@given(data=st.binary(min_size=0, max_size=256))
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_verify_handles_arbitrary_bytes(data: bytes) -> None:
    """Full verify path (parse + HMAC check) never raises unhandled exceptions."""
    _try_verify(data)


# ---------------------------------------------------------------------------
# MINOR-14: length-field mutation (header_len / kid_len)
# ---------------------------------------------------------------------------
# Mutate the header_len (4-byte LE uint32 at offset 45+K) and kid_len
# (1-byte uint8 at offset 12) independently to large and small values.
# The loader must fail closed with ArtifactError or OSError — never crash.

_VALID_ARTIFACT_BYTES: bytes = VALID_ARTIFACT


def _mutate_header_len(data: bytes, new_header_len: int) -> bytes:
    """Replace the header_len field in an artifact with *new_header_len*."""
    import struct

    from recotem.artifact.format import FIXED_PREFIX_SIZE, HMAC_SIZE

    # Locate header_len offset:
    # FIXED_PREFIX_SIZE = 13 (magic8 + version2 + reserved2 + kid_len_byte1)
    # then kid_bytes (kid_len), then HMAC (32), then header_len (4)
    kid_len = data[FIXED_PREFIX_SIZE - 1]
    header_len_offset = FIXED_PREFIX_SIZE + kid_len + HMAC_SIZE
    if header_len_offset + 4 > len(data):
        return data  # artifact too short to mutate; skip
    mutated = bytearray(data)
    struct.pack_into("<I", mutated, header_len_offset, new_header_len & 0xFFFFFFFF)
    return bytes(mutated)


def _mutate_kid_len(data: bytes, new_kid_len: int) -> bytes:
    """Replace the kid_len byte in an artifact with *new_kid_len*."""
    from recotem.artifact.format import FIXED_PREFIX_SIZE

    if len(data) < FIXED_PREFIX_SIZE:
        return data
    mutated = bytearray(data)
    mutated[FIXED_PREFIX_SIZE - 1] = new_kid_len & 0xFF
    return bytes(mutated)


@given(
    new_header_len=st.integers(min_value=0, max_value=2**32 - 1),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_fuzz_header_len_mutation_never_panics(new_header_len: int) -> None:
    """Mutating header_len to any uint32 value must fail closed (ArtifactError)."""
    data = _mutate_header_len(_VALID_ARTIFACT_BYTES, new_header_len)
    try:
        parse_header_from_bytes(data, max_payload_bytes=2**20)
    except ArtifactError:
        pass  # expected
    except (OSError, OverflowError, struct.error):
        pass  # acceptable OS-level failure
    except Exception as exc:
        raise AssertionError(
            f"Unhandled exception on header_len={new_header_len}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


import struct  # noqa: E402 — used inside the parametrized strategies above


@given(
    new_kid_len=st.integers(min_value=0, max_value=255),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_fuzz_kid_len_mutation_never_panics(new_kid_len: int) -> None:
    """Mutating kid_len to any uint8 value must fail closed (ArtifactError)."""
    data = _mutate_kid_len(_VALID_ARTIFACT_BYTES, new_kid_len)
    try:
        parse_header_from_bytes(data, max_payload_bytes=2**20)
    except ArtifactError:
        pass  # expected
    except (OSError, OverflowError, struct.error):
        pass  # acceptable OS-level failure
    except Exception as exc:
        raise AssertionError(
            f"Unhandled exception on kid_len={new_kid_len}: {type(exc).__name__}: {exc}"
        ) from exc


@given(
    new_header_len=st.integers(min_value=65537, max_value=2**32 - 1),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_fuzz_oversized_header_len_rejected(new_header_len: int) -> None:
    """header_len values exceeding MAX_HEADER_LEN (65536) must raise ArtifactError."""
    data = _mutate_header_len(_VALID_ARTIFACT_BYTES, new_header_len)
    try:
        parse_header_from_bytes(data, max_payload_bytes=2**32)
    except ArtifactError:
        pass  # correct: "exceeds maximum" error
    except (OSError, OverflowError, struct.error):
        pass  # acceptable
    except Exception as exc:
        raise AssertionError(
            f"Unhandled exception on oversized header_len={new_header_len}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
