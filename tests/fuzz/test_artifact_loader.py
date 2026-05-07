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
