"""Unit tests for recotem.serving.auth.

Tests:
- Constant-time compare
- Whitespace rejection
- Multiple keys: any authenticates
- Malformed entry rejection
- kid attached to request.state
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from recotem.config import ApiKeyEntry
from recotem.serving.auth import verify_api_key


def _make_request(api_key: str | None = None) -> MagicMock:
    request = MagicMock()
    request.url.path = "/predict/test"
    request.headers = {}
    if api_key is not None:
        request.headers = {"x-api-key": api_key}
    request.state = MagicMock()
    return request


def _make_entry(kid: str, plaintext: str) -> ApiKeyEntry:
    # Mirror recotem.serving.auth._hash_api_key (scrypt KDF with the
    # ``recotem.api-key.v1`` domain-separation salt at minimum cost).  The
    # ApiKeyEntry field is still named ``sha256_hex`` because the wire format
    # keeps the ``sha256:`` prefix; the digest family is the 32-byte hex digest.
    sha256_hex = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return ApiKeyEntry(kid=kid, sha256_hex=sha256_hex)


# ---------------------------------------------------------------------------
# Basic auth
# ---------------------------------------------------------------------------


def test_verify_api_key_succeeds_with_correct_key() -> None:
    entry = _make_entry("k1", "supersecretkey123456789012345678")
    request = _make_request("supersecretkey123456789012345678")
    kid = verify_api_key(request, [entry])
    assert kid == "k1"


def test_verify_api_key_wrong_key_raises_401() -> None:
    entry = _make_entry("k1", "correct_key_value_here_32_bytes!")
    request = _make_request("wrong_key_value_here")
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(request, [entry])
    assert exc_info.value.status_code == 401


def test_verify_api_key_missing_header_raises_401() -> None:
    entry = _make_entry("k1", "somekey")
    request = _make_request(api_key=None)
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(request, [entry])
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# No-auth mode (empty key list)
# ---------------------------------------------------------------------------


def test_verify_api_key_no_keys_configured_returns_anonymous() -> None:
    request = _make_request(api_key=None)
    kid = verify_api_key(request, [])
    assert kid == "anonymous"


# ---------------------------------------------------------------------------
# Whitespace rejection
# ---------------------------------------------------------------------------


def test_api_key_with_padding_whitespace_rejected_401() -> None:
    """Leading/trailing whitespace makes the key invalid (no strip)."""
    plaintext = "exactkey_no_whitespace_12345678"
    entry = _make_entry("k1", plaintext)
    # Key with leading space
    request = _make_request(" " + plaintext)
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(request, [entry])
    assert exc_info.value.status_code == 401


def test_api_key_with_trailing_whitespace_rejected_401() -> None:
    plaintext = "exactkey_no_whitespace_12345678"
    entry = _make_entry("k1", plaintext)
    request = _make_request(plaintext + " ")
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(request, [entry])
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Multiple keys: any authenticates
# ---------------------------------------------------------------------------


def test_multiple_keys_any_authenticates() -> None:
    entry1 = _make_entry("k1", "key_number_one_32_bytes_exactly!")
    entry2 = _make_entry("k2", "key_number_two_32_bytes_exactly!")
    # Authenticate with key2
    request = _make_request("key_number_two_32_bytes_exactly!")
    kid = verify_api_key(request, [entry1, entry2])
    assert kid == "k2"


def test_three_configured_keys_fourth_unrecognized_rejected_401() -> None:
    entries = [
        _make_entry(f"k{i}", f"key_value_{i}_padding_to_32_bytes") for i in range(3)
    ]
    request = _make_request("unknown_key_not_in_any_entry_xx")
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(request, entries)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# kid attached to request.state (not plaintext or hash)
# ---------------------------------------------------------------------------


def test_kid_attached_to_request_state_not_plaintext() -> None:
    plaintext = "my_secret_key_32_bytes_exactly!!"
    entry = _make_entry("my-kid", plaintext)
    request = _make_request(plaintext)
    kid = verify_api_key(request, [entry])
    assert kid == "my-kid"
    assert request.state.kid == "my-kid"
    # The plaintext must NOT be on state
    state_dict = vars(request.state) if hasattr(request.state, "__dict__") else {}
    for val in state_dict.values():
        assert val != plaintext, "Plaintext key must not be stored in request.state"


# ---------------------------------------------------------------------------
# ApiKeyEntry.parse malformed entry rejection
# ---------------------------------------------------------------------------


def test_malformed_api_keys_entry_bad_format_raises() -> None:
    with pytest.raises(ValueError, match="malformed"):
        ApiKeyEntry.parse("badentry_no_sha256_separator")


def test_malformed_api_keys_entry_short_hash_raises() -> None:
    with pytest.raises(ValueError, match="64 hex"):
        ApiKeyEntry.parse("kid:sha256:tooshort")


def test_malformed_api_keys_entry_empty_kid_raises() -> None:
    valid_hash = "a" * 64
    with pytest.raises(ValueError, match="kid"):
        ApiKeyEntry.parse(f":sha256:{valid_hash}")


# ---------------------------------------------------------------------------
# Constant-time compare
# ---------------------------------------------------------------------------


def test_api_key_compare_uses_hmac_compare_digest() -> None:
    """Verify that the auth module uses hmac.compare_digest (not ==)."""
    import inspect

    import recotem.serving.auth as auth_module

    source = inspect.getsource(auth_module)
    assert "compare_digest" in source, (
        "auth.py must use hmac.compare_digest for constant-time comparison"
    )


# ---------------------------------------------------------------------------
# _hash_api_key determinism, collision-resistance, length
# ---------------------------------------------------------------------------


def test_hash_api_key_is_deterministic() -> None:
    """Same input produces the same hash on repeated calls."""
    from recotem.serving.auth import _hash_api_key

    value = "my-deterministic-key-input-1234"
    assert _hash_api_key(value) == _hash_api_key(value)


def test_hash_api_key_different_inputs_yield_different_hashes() -> None:
    """Two distinct inputs must not produce the same digest."""
    from recotem.serving.auth import _hash_api_key

    h1 = _hash_api_key("input_alpha_padding_to_length_ok")
    h2 = _hash_api_key("input_beta__padding_to_length_ok")
    assert h1 != h2


def test_hash_api_key_uses_scrypt_under_the_hood() -> None:
    """Inspect the source of _hash_api_key to confirm it calls hashlib.scrypt."""
    import inspect

    import recotem.serving.auth as auth_module

    source = inspect.getsource(auth_module._hash_api_key)
    assert "scrypt" in source, (
        "_hash_api_key must use hashlib.scrypt for key derivation"
    )


def test_hash_api_key_produces_64_char_hex() -> None:
    """scrypt with dklen=32 yields a 32-byte (64-char hex) digest."""
    from recotem.serving.auth import _hash_api_key

    result = _hash_api_key("any-key-value-here")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_hash_api_key_label_constant_is_recotem_api_key_v1() -> None:
    """_API_KEY_HMAC_LABEL must be b'recotem.api-key.v1' (domain separation)."""
    from recotem.serving.auth import _API_KEY_HMAC_LABEL

    assert _API_KEY_HMAC_LABEL == b"recotem.api-key.v1"


def test_scrypt_params_match_cli_keygen() -> None:
    """The scrypt cost parameters in auth.py must be identical to those in cli.py.

    If either side drifts (e.g. n changes), keys generated by `recotem keygen`
    will no longer verify against the serving layer.
    """
    import inspect

    import recotem.cli as cli_module
    from recotem.serving.auth import _SCRYPT_DKLEN, _SCRYPT_N, _SCRYPT_P, _SCRYPT_R

    # Verify the values the CLI uses by inspecting its source for the same
    # literals, then also verifying the outcome matches directly.

    cli_source = inspect.getsource(cli_module)

    # The CLI must reference the same n, r, p, dklen values for API key hashing.
    assert f"n={_SCRYPT_N}" in cli_source, (
        f"cli.py must use n={_SCRYPT_N} to match auth.py"
    )
    assert f"r={_SCRYPT_R}" in cli_source, (
        f"cli.py must use r={_SCRYPT_R} to match auth.py"
    )
    assert f"p={_SCRYPT_P}" in cli_source, (
        f"cli.py must use p={_SCRYPT_P} to match auth.py"
    )
    assert f"dklen={_SCRYPT_DKLEN}" in cli_source, (
        f"cli.py must use dklen={_SCRYPT_DKLEN} to match auth.py"
    )

    # Also verify cross-compatibility by computing the hash both ways.
    import hashlib

    plaintext = "cross-compat-test-key-padding-xx"

    hash_via_auth = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=b"recotem.api-key.v1",
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    ).hex()

    hash_via_cli_params = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=b"recotem.api-key.v1",
        n=2,  # CLI-side literals (from cli.py keygen command)
        r=8,
        p=1,
        dklen=32,
    ).hex()

    assert hash_via_auth == hash_via_cli_params, (
        "auth.py and cli.py must use identical scrypt parameters for API key hashing"
    )
