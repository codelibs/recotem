"""Pin what RECOTEM_API_KEYS actually enforces about duplicates.

``serving/auth.py`` used to assert, in a comment beside the match fold, that
startup "rejects duplicate sha256 hashes so at most one entry can ever match".
It does not: ``ServeConfig.from_env`` rejects a duplicate *kid* and accepts two
entries whose *hashes* are equal.  No bypass follows — the fold is written to
tolerate multiple matches and returns the first — but a future maintainer who
believed the comment could write code that depends on uniqueness.

These tests pin both halves so the comment and the code cannot drift apart
again, in either direction:

* if someone makes ``from_env`` reject duplicate hashes, the "accepted" test
  fails and forces the comment to be updated with it;
* if someone makes the fold assume a unique match, the first-match test fails.
"""

from __future__ import annotations

import pytest

from recotem.config import ApiKeyEntry, ConfigError, ServeConfig

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_SIGNING = "dev:" + "ab" * 32


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", _SIGNING)
    monkeypatch.delenv("RECOTEM_API_KEYS", raising=False)


def test_duplicate_kid_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The invariant that IS enforced: a kid may appear at most once."""
    monkeypatch.setenv("RECOTEM_API_KEYS", f"k1:sha256:{_HASH_A},k1:sha256:{_HASH_B}")
    with pytest.raises(ConfigError, match="duplicate kid"):
        ServeConfig.from_env()


def test_duplicate_hash_under_distinct_kids_is_ACCEPTED(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The invariant that is NOT enforced — the point of this file.

    Two labels for one plaintext key load fine.  If this ever starts raising,
    the fold's comment in serving/auth.py must be updated in the same change.
    """
    monkeypatch.setenv("RECOTEM_API_KEYS", f"k1:sha256:{_HASH_A},k2:sha256:{_HASH_A}")
    cfg = ServeConfig.from_env()
    assert [e.kid for e in cfg.api_keys] == ["k1", "k2"]
    assert cfg.api_keys[0].sha256_hex == cfg.api_keys[1].sha256_hex


def test_colliding_hashes_resolve_to_the_first_kid_deterministically() -> None:
    """The fold must tolerate >1 match and attribute to the FIRST entry.

    Exercised directly against ``verify_api_key`` so the guarantee is the one
    the request path actually gives, not one inferred from the config layer.
    """
    from recotem.serving.auth import _hash_api_key, verify_api_key

    plaintext = "z" * 43  # >= _API_KEY_MIN_LEN, valid shape
    shared = _hash_api_key(plaintext)
    entries = [
        ApiKeyEntry(kid="first", sha256_hex=shared),
        ApiKeyEntry(kid="second", sha256_hex=shared),
    ]

    class _Req:
        headers = {"x-api-key": plaintext}
        url = type("U", (), {"path": "/v1/recipes/x:recommend"})()
        client = None

        class state:  # noqa: N801 - mimics Starlette's request.state
            kid: str | None = None

    req = _Req()
    assert verify_api_key(req, entries) == "first"  # type: ignore[arg-type]
    assert req.state.kid == "first"

    # Reversing the order flips the attribution: it is positional, not by luck.
    req2 = _Req()
    assert verify_api_key(req2, list(reversed(entries))) == "second"  # type: ignore[arg-type]
