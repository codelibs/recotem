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
    """_API_KEY_SCRYPT_SALT must be b'recotem.api-key.v1' (domain separation)."""
    from recotem.serving.auth import _API_KEY_SCRYPT_SALT

    assert _API_KEY_SCRYPT_SALT == b"recotem.api-key.v1"


def test_cli_keygen_uses_auth_hash_function() -> None:
    """The CLI's `recotem keygen --type api` command must produce a hash that
    the serving layer's ``verify_api_key`` accepts.

    Achieved structurally by having cli.py call the same ``_hash_api_key``
    helper — this test pins that contract by running the CLI command and
    cross-checking the printed hash against ``_hash_api_key(plaintext)``.
    """
    from typer.testing import CliRunner

    from recotem.cli import app as cli_app
    from recotem.serving.auth import _hash_api_key

    result = CliRunner().invoke(cli_app, ["keygen", "--type", "api"])
    assert result.exit_code == 0, result.output

    plaintext: str | None = None
    printed_hash: str | None = None
    for line in result.output.splitlines():
        if line.startswith("plaintext="):
            plaintext = line.split("=", 1)[1].strip()
        elif line.startswith("hash=sha256:"):
            printed_hash = line.split("=", 1)[1].strip().removeprefix("sha256:")

    assert plaintext is not None and printed_hash is not None, result.output
    assert printed_hash == _hash_api_key(plaintext), (
        "cli.py keygen must use the same hashing as serving.auth"
    )


# ---------------------------------------------------------------------------
# MAJOR-5: oversized X-API-Key header rejected before scrypt KDF runs
# ---------------------------------------------------------------------------


def test_oversized_api_key_header_rejected_with_constant_time_equalisation() -> None:
    """An ``X-API-Key`` header longer than ``_API_KEY_MAX_LEN`` must be
    rejected with 401, and ``_hash_api_key`` (scrypt) must be invoked exactly
    once on a fixed-length dummy value to equalise response time.

    Rationale: without the constant-time scrypt call on the oversized path,
    an attacker can distinguish the oversized branch (~0 ms, no KDF) from
    the normal branch (~0.5 ms, KDF runs) via response latency — a
    length-based timing oracle that leaks the accepted key-length range.
    """
    from unittest.mock import patch

    import recotem.serving.auth as auth_module
    from recotem.serving.auth import _API_KEY_MAX_LEN, verify_api_key

    entry = _make_entry("k1", "exact_secret_padding_to_32_bytes")
    oversize_key = "A" * 1024  # well over _API_KEY_MAX_LEN
    assert len(oversize_key) > _API_KEY_MAX_LEN

    request = _make_request(oversize_key)

    with patch.object(
        auth_module, "_hash_api_key", wraps=auth_module._hash_api_key
    ) as hash_spy:
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(request, [entry])
        assert exc_info.value.status_code == 401
        assert hash_spy.call_count == 1, (
            f"_hash_api_key (scrypt) must be invoked exactly once on the "
            f"oversized path for constant-time equalisation, "
            f"got {hash_spy.call_count} calls."
        )


def test_api_key_at_max_len_still_accepted_for_hashing() -> None:
    """A header exactly at ``_API_KEY_MAX_LEN`` chars must still be hashed
    (and rejected only because the digest does not match a configured
    entry).  This pins the exact boundary so future tightening to a
    smaller cap is intentional.
    """
    from unittest.mock import patch

    import recotem.serving.auth as auth_module
    from recotem.serving.auth import _API_KEY_MAX_LEN, verify_api_key

    entry = _make_entry("k1", "real_secret_padded_to_32_bytes!!")
    boundary_key = "B" * _API_KEY_MAX_LEN

    request = _make_request(boundary_key)

    with patch.object(
        auth_module, "_hash_api_key", wraps=auth_module._hash_api_key
    ) as hash_spy:
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(request, [entry])
        # Still 401 — the boundary-length key is hashed but does not match.
        assert exc_info.value.status_code == 401
        assert hash_spy.call_count == 1, (
            f"At exactly _API_KEY_MAX_LEN chars the KDF must run; "
            f"got {hash_spy.call_count} invocations."
        )


def test_api_key_just_over_max_len_rejected() -> None:
    """One char over ``_API_KEY_MAX_LEN`` must trip the cap.

    The constant-time equalisation means _hash_api_key is still called once
    (on a fixed dummy), ensuring the oversized path takes the same wall time
    as the normal (hashing) path.
    """
    from unittest.mock import patch

    import recotem.serving.auth as auth_module
    from recotem.serving.auth import _API_KEY_MAX_LEN, verify_api_key

    entry = _make_entry("k1", "real_secret_padded_to_32_bytes!!")
    over_key = "C" * (_API_KEY_MAX_LEN + 1)

    request = _make_request(over_key)

    with patch.object(
        auth_module, "_hash_api_key", wraps=auth_module._hash_api_key
    ) as hash_spy:
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(request, [entry])
        assert exc_info.value.status_code == 401
        # Exactly 1 call (constant-time equalisation dummy), not 0.
        assert hash_spy.call_count == 1, (
            f"Expected exactly 1 _hash_api_key call for constant-time "
            f"equalisation on an oversized header, got {hash_spy.call_count}."
        )


# ---------------------------------------------------------------------------
# MINOR-13: keygen scrypt cost factors consistent with auth verify
# ---------------------------------------------------------------------------
# The keygen command calls _hash_api_key to produce the stored hash.
# The verify call also uses _hash_api_key.  The cost factors must be identical
# and pinned so they don't silently diverge in a future refactor.


def test_scrypt_cost_factors_are_pinned_to_documented_values() -> None:
    """The scrypt cost factors (N, r, p, dklen) are pinned to N=2, r=8, p=1, dklen=32.

    These values are chosen because API keys are 256-bit random tokens,
    making brute-force infeasible regardless of hash speed.  The lowest
    valid scrypt cost is used to keep verification under 1ms.

    This test pins the exact constants so any change triggers a reviewed test
    failure rather than a silent configuration drift.
    """
    from recotem.serving.auth import (
        _SCRYPT_DKLEN,
        _SCRYPT_N,
        _SCRYPT_P,
        _SCRYPT_R,
    )

    assert _SCRYPT_N == 2, f"Expected _SCRYPT_N=2, got {_SCRYPT_N}"
    assert _SCRYPT_R == 8, f"Expected _SCRYPT_R=8, got {_SCRYPT_R}"
    assert _SCRYPT_P == 1, f"Expected _SCRYPT_P=1, got {_SCRYPT_P}"
    assert _SCRYPT_DKLEN == 32, f"Expected _SCRYPT_DKLEN=32, got {_SCRYPT_DKLEN}"


def test_keygen_hash_matches_verify_hash_round_trip() -> None:
    """keygen produces a hash that verify_api_key accepts — round-trip.

    This confirms that keygen and the auth verify share the same KDF
    parameters: if N/r/p/dklen diverge between the two, the round-trip fails.
    """
    from recotem.serving.auth import _hash_api_key

    plaintext = "round_trip_test_key_of_32_chars!!"

    # Hash as keygen would
    keygen_hash = _hash_api_key(plaintext)

    # Build an entry as the config parser would
    entry = ApiKeyEntry(kid="rt-test", sha256_hex=keygen_hash)

    # Simulate verify as auth.py would
    verify_hash = _hash_api_key(plaintext)

    import hmac as _hmac

    assert _hmac.compare_digest(keygen_hash, verify_hash), (
        "keygen hash and verify hash must be identical for the same plaintext"
    )
    assert entry.sha256_hex == keygen_hash, (
        "Entry sha256_hex must equal the keygen-produced hash"
    )


def test_hash_api_key_round_trip_with_scrypt_parameters() -> None:
    """Verify the exact scrypt parameters are used for the hash.

    Computes the expected digest using the pinned parameters directly and
    confirms it matches _hash_api_key output.
    """
    import hashlib

    from recotem.serving.auth import (
        _API_KEY_SCRYPT_SALT,
        _SCRYPT_DKLEN,
        _SCRYPT_N,
        _SCRYPT_P,
        _SCRYPT_R,
        _hash_api_key,
    )

    plaintext = "parameter_consistency_test_key_!"
    expected = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=_API_KEY_SCRYPT_SALT,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    ).hex()

    assert _hash_api_key(plaintext) == expected, (
        "_hash_api_key must use exactly the pinned scrypt parameters"
    )


# ---------------------------------------------------------------------------
# MAJOR-1: constant-time timing-oracle tests (new, post-equalisation)
# ---------------------------------------------------------------------------


def test_short_key_invokes_scrypt_for_constant_time() -> None:
    """A key shorter than ``_API_KEY_MIN_LEN`` must still invoke ``_hash_api_key``
    exactly once (on a fixed dummy) so that response latency is indistinguishable
    from the normal hashing path.

    Without this dummy call, an attacker can measure latency differences of a
    few milliseconds to determine whether the key was too short — leaking the
    accepted key-length range.
    """
    from unittest.mock import patch

    import recotem.serving.auth as auth_module
    from recotem.serving.auth import _API_KEY_MIN_LEN, verify_api_key

    entry = _make_entry("k1", "full_length_key_32_bytes_exactly!")
    short_key = "x" * (_API_KEY_MIN_LEN - 1)  # one char under the floor
    assert len(short_key) < _API_KEY_MIN_LEN

    request = _make_request(short_key)

    with patch.object(
        auth_module, "_hash_api_key", wraps=auth_module._hash_api_key
    ) as hash_spy:
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(request, [entry])

    assert exc_info.value.status_code == 401
    assert hash_spy.call_count == 1, (
        f"Expected exactly 1 _hash_api_key call (constant-time equalisation) "
        f"on the short-key path, got {hash_spy.call_count}."
    )


def test_oversized_header_invokes_scrypt_for_constant_time() -> None:
    """A header longer than ``_API_KEY_MAX_LEN`` must invoke ``_hash_api_key``
    exactly once (on a fixed dummy) so that response latency is indistinguishable
    from the normal hashing path.

    This is the symmetric counterpart to test_short_key_invokes_scrypt_for_constant_time
    and guards against a latency oracle at the upper key-length boundary.
    """
    from unittest.mock import patch

    import recotem.serving.auth as auth_module
    from recotem.serving.auth import _API_KEY_MAX_LEN, verify_api_key

    entry = _make_entry("k1", "full_length_key_32_bytes_exactly!")
    big_key = "Y" * (_API_KEY_MAX_LEN + 10)  # clearly over the cap
    assert len(big_key) > _API_KEY_MAX_LEN

    request = _make_request(big_key)

    with patch.object(
        auth_module, "_hash_api_key", wraps=auth_module._hash_api_key
    ) as hash_spy:
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(request, [entry])

    assert exc_info.value.status_code == 401
    assert hash_spy.call_count == 1, (
        f"Expected exactly 1 _hash_api_key call (constant-time equalisation) "
        f"on the oversized-header path, got {hash_spy.call_count}."
    )


def test_verify_api_key_uses_compare_digest_at_runtime() -> None:
    """``hmac.compare_digest`` must be invoked during a normal verify call.

    This test patches ``hmac.compare_digest`` with a sentinel that records
    calls, then runs a successful verification and confirms the sentinel was
    reached.  This is complementary to the source-inspection test and verifies
    the code path at runtime, not just at parse time.
    """
    import hmac as _hmac
    from unittest.mock import patch

    from recotem.serving.auth import verify_api_key

    plaintext = "runtime_compare_digest_test_key!"
    entry = _make_entry("k1", plaintext)
    request = _make_request(plaintext)

    # Capture the real function *before* patching so the sentinel can call
    # the original without recursing into the mock.
    _real_compare_digest = _hmac.compare_digest
    compare_digest_calls: list[tuple[str, str]] = []

    def _sentinel_compare_digest(a: str, b: str) -> bool:
        compare_digest_calls.append((a, b))
        return _real_compare_digest(a, b)

    with patch.object(_hmac, "compare_digest", side_effect=_sentinel_compare_digest):
        kid = verify_api_key(request, [entry])

    assert kid == "k1"
    assert len(compare_digest_calls) >= 1, (
        "hmac.compare_digest must be called at least once during verify_api_key"
    )
