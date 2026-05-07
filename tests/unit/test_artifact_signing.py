"""Unit tests for recotem.artifact.signing.

Tests:
- Sign+verify roundtrip
- One-byte payload tamper rejection
- HMAC with wrong key rejected
- Unknown kid rejected
- SafeUnpickler allow-list (parameterised gadget rejection)
- KeyRing rotation semantics
"""

from __future__ import annotations

import pytest

from recotem.artifact.format import ArtifactError
from recotem.artifact.signing import (
    KeyRing,
    SafeUnpickler,
    compute_hmac,
    unpickle_payload,
    verify_hmac,
)
from tests.conftest import ACTIVE_KEY_HEX, OLD_KEY_HEX

# ---------------------------------------------------------------------------
# KeyRing construction
# ---------------------------------------------------------------------------


def test_key_ring_active_kid_is_first() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}", f"old:{OLD_KEY_HEX}")
    assert kr.active_kid == "active"


def test_key_ring_get_known_kid_returns_bytes() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    assert kr.get("active") == bytes.fromhex(ACTIVE_KEY_HEX)


def test_key_ring_get_unknown_kid_returns_none() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    assert kr.get("nonexistent") is None


def test_key_ring_kids_returns_all_in_order() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}", f"old:{OLD_KEY_HEX}")
    assert kr.kids() == ["active", "old"]


def test_key_ring_duplicate_kid_raises() -> None:
    with pytest.raises(ArtifactError, match="duplicate"):
        KeyRing(f"active:{ACTIVE_KEY_HEX}", f"active:{OLD_KEY_HEX}")


def test_key_ring_empty_raises() -> None:
    with pytest.raises(ArtifactError):
        KeyRing()


def test_key_ring_malformed_entry_raises() -> None:
    with pytest.raises(ArtifactError, match="malformed"):
        KeyRing("no-colon-here")


def test_key_ring_wrong_key_length_raises() -> None:
    with pytest.raises(ArtifactError, match="32 bytes"):
        KeyRing("kid:" + "aa" * 16)  # only 16 bytes


def test_key_ring_comma_separated_single_string() -> None:
    """KeyRing accepts a comma-separated string as a single argument."""
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX},old:{OLD_KEY_HEX}")
    assert kr.kids() == ["active", "old"]


def test_key_ring_fingerprint_returns_8_hex_chars() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    fp = kr.fingerprint("active")
    assert fp is not None
    assert len(fp) == 8
    assert all(c in "0123456789abcdef" for c in fp)


def test_key_ring_fingerprint_unknown_kid_returns_none() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    assert kr.fingerprint("ghost") is None


# ---------------------------------------------------------------------------
# Sign + verify roundtrip
# ---------------------------------------------------------------------------


def test_sign_verify_roundtrip() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    kid = "active"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"recipe_name":"test"}'
    payload = b"arbitrary payload"
    key = kr.get(kid)
    assert key is not None
    digest = compute_hmac(key, kid_bytes, header_json, payload)
    verify_hmac(kr, kid, kid_bytes, header_json, payload, digest)  # no exception


def test_one_byte_tamper_rejected() -> None:
    """Flipping one byte in payload causes verify to fail."""
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    kid = "active"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"recipe_name":"test"}'
    payload = bytearray(b"original payload bytes")
    key = kr.get(kid)
    assert key is not None
    digest = compute_hmac(key, kid_bytes, header_json, bytes(payload))
    payload[0] ^= 0xFF  # flip one byte
    with pytest.raises(ArtifactError, match="HMAC"):
        verify_hmac(kr, kid, kid_bytes, header_json, bytes(payload), digest)


def test_hmac_valid_over_wrong_key_rejected() -> None:
    """Digest computed with a different key fails verify."""
    kr_correct = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    kr_wrong = KeyRing(f"active:{OLD_KEY_HEX}")  # same kid, different key
    kid = "active"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"x":1}'
    payload = b"data"
    key_wrong = kr_wrong.get(kid)
    assert key_wrong is not None
    bad_digest = compute_hmac(key_wrong, kid_bytes, header_json, payload)
    with pytest.raises(ArtifactError, match="HMAC"):
        verify_hmac(kr_correct, kid, kid_bytes, header_json, payload, bad_digest)


def test_hmac_valid_with_unknown_kid_rejected() -> None:
    """Artifact signed with a kid not in the KeyRing raises ArtifactError."""
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    kid = "ghost"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"x":1}'
    payload = b"data"
    fake_digest = b"\x00" * 32
    with pytest.raises(ArtifactError, match="unknown kid"):
        verify_hmac(kr, kid, kid_bytes, header_json, payload, fake_digest)


# ---------------------------------------------------------------------------
# KeyRing rotation: old key still verifies
# ---------------------------------------------------------------------------


def test_old_key_verifies_with_two_key_ring() -> None:
    """An artifact signed with the old key verifies against a two-key ring."""
    kr_old_only = KeyRing(f"old:{OLD_KEY_HEX}")
    kid = "old"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"recipe_name":"legacy"}'
    payload = b"payload"
    key_old = kr_old_only.get(kid)
    assert key_old is not None
    digest = compute_hmac(key_old, kid_bytes, header_json, payload)

    kr_both = KeyRing(f"active:{ACTIVE_KEY_HEX}", f"old:{OLD_KEY_HEX}")
    verify_hmac(kr_both, kid, kid_bytes, header_json, payload, digest)  # no exception


# ---------------------------------------------------------------------------
# SafeUnpickler allow-list (parameterised gadget rejection)
# ---------------------------------------------------------------------------

_GADGETS = [
    ("os", "system"),
    ("subprocess", "Popen"),
    ("numpy.testing", "run_module_suite"),
    ("builtins", "exec"),
    ("posix", "system"),
]


@pytest.mark.parametrize("module,name", _GADGETS)
def test_payload_class_outside_whitelist_rejected(module: str, name: str) -> None:
    """SafeUnpickler.find_class rejects classes not in the allow-list."""
    import io

    unpickler = SafeUnpickler(io.BytesIO(b""))
    with pytest.raises(ArtifactError, match="not allowed"):
        unpickler.find_class(module, name)


def test_safe_unpickler_allows_builtins_dict() -> None:
    """builtins.dict is in the allow-list and must not be blocked."""
    import pickle  # noqa: S403

    payload = pickle.dumps({"x": 1}, protocol=4)  # noqa: S301
    result = unpickle_payload(payload)
    assert result == {"x": 1}


def test_safe_unpickler_allows_builtins_list() -> None:
    """builtins.list is in the allow-list."""
    import pickle  # noqa: S403

    payload = pickle.dumps([1, 2, 3], protocol=4)  # noqa: S301
    result = unpickle_payload(payload)
    assert result == [1, 2, 3]


def test_unpickle_with_disallowed_class_raises_artifact_error() -> None:
    """A pickle stream referencing a disallowed class raises ArtifactError."""
    # Build a pickle stream that calls os.system
    import os as _os
    import pickle  # noqa: S403

    class _Exploit:
        def __reduce__(self):
            return (_os.system, ("echo pwned",))

    payload = pickle.dumps(_Exploit(), protocol=4)  # noqa: S301
    with pytest.raises(ArtifactError, match="not allowed"):
        unpickle_payload(payload)
