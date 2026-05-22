# tests/unit/test_v1_auth_wrong_key.py
"""T2 + T8: wrong-key (non-empty but invalid) 401 and KeyRing rotation across
all 4 recommend verbs.

T2: Parametrize over all 4 verbs, send a valid-length but wrong X-API-Key,
    assert 401 with code=INVALID_API_KEY.

T8: Configure two API keys (old + new).  Assert that both key holders reach
    :recommend with 200, and that a third key gets 401.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FAKE_SHA256_HEX = "c" * 64  # 64 lowercase hex chars


def _hash_key(plaintext: str) -> str:
    return hashlib.scrypt(
        plaintext.encode(),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()


def _make_api_entry(plaintext: str, kid: str = "k1") -> ApiKeyEntry:
    return ApiKeyEntry(kid=kid, sha256_hex=_hash_key(plaintext))


def _make_loaded_entry(name: str = "demo") -> ModelEntry:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    rec._mapper = MagicMock()
    rec._mapper.item_id_to_index = {"i1": 0}
    rec.get_recommendation_for_new_user.return_value = [("i1", 0.9)]
    return ModelEntry(
        name=name,
        recommender=rec,
        header={},
        kid="active",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )


def _build_client(api_entries: list[ApiKeyEntry]) -> TestClient:
    registry = ModelRegistry()
    registry.replace("demo", _make_loaded_entry("demo"))
    return TestClient(build_v1_app(registry, api_keys=api_entries))


# ---------------------------------------------------------------------------
# Minimal valid request bodies for each verb
# ---------------------------------------------------------------------------

_VERB_BODIES: dict[str, dict] = {
    "recommend": {"user_id": "u1", "limit": 1},
    "recommend-related": {"seed_items": ["i1"], "limit": 1},
    "batch-recommend": {"requests": [{"user_id": "u1", "limit": 1}]},
    "batch-recommend-related": {"requests": [{"seed_items": ["i1"], "limit": 1}]},
}

_VALID_PLAINTEXT = "valid_api_key_for_test_32_bytes!"
_WRONG_PLAINTEXT = "wrong_api_key_for_test_32_bytes!"


# ---------------------------------------------------------------------------
# T2: wrong-key 401 across all 4 verbs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", list(_VERB_BODIES.keys()))
def test_wrong_key_returns_401_with_invalid_api_key_code(verb: str) -> None:
    """A non-empty but wrong X-API-Key must return 401 INVALID_API_KEY on every verb."""
    api_entry = _make_api_entry(_VALID_PLAINTEXT, kid="k1")
    client = _build_client([api_entry])

    url = f"/v1/recipes/demo:{verb}"
    r = client.post(
        url, json=_VERB_BODIES[verb], headers={"X-API-Key": _WRONG_PLAINTEXT}
    )

    assert r.status_code == 401, (
        f"Expected 401 for verb {verb!r} with wrong key; got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("code") == "INVALID_API_KEY", (
        f"Expected code=INVALID_API_KEY for verb {verb!r}; got {body!r}"
    )
    assert "detail" in body, f"Response must include 'detail' field; got {body!r}"


# ---------------------------------------------------------------------------
# T8: KeyRing rotation — both keys pass, unknown key fails
# ---------------------------------------------------------------------------

_KEY_OLD = "old_api_key_for_rotation_test!!X"  # 32 chars exactly
_KEY_NEW = "new_api_key_for_rotation_test!!Y"  # 32 chars exactly
_KEY_NEITHER = "neither_key_for_rotation_test!!Z"  # 32 chars exactly


def test_keyring_old_key_accepted_on_recommend() -> None:
    """Old key (first entry) is accepted with 200 on :recommend."""
    entry_old = _make_api_entry(_KEY_OLD, kid="old")
    entry_new = _make_api_entry(_KEY_NEW, kid="new")
    client = _build_client([entry_old, entry_new])

    r = client.post(
        "/v1/recipes/demo:recommend",
        json=_VERB_BODIES["recommend"],
        headers={"X-API-Key": _KEY_OLD},
    )
    assert r.status_code == 200, (
        f"Old key must be accepted; got {r.status_code}: {r.text}"
    )


def test_keyring_new_key_accepted_on_recommend() -> None:
    """New key (second entry) is accepted with 200 on :recommend."""
    entry_old = _make_api_entry(_KEY_OLD, kid="old")
    entry_new = _make_api_entry(_KEY_NEW, kid="new")
    client = _build_client([entry_old, entry_new])

    r = client.post(
        "/v1/recipes/demo:recommend",
        json=_VERB_BODIES["recommend"],
        headers={"X-API-Key": _KEY_NEW},
    )
    assert r.status_code == 200, (
        f"New key must be accepted; got {r.status_code}: {r.text}"
    )


def test_keyring_neither_key_rejected_401() -> None:
    """A key that matches neither entry returns 401 INVALID_API_KEY."""
    entry_old = _make_api_entry(_KEY_OLD, kid="old")
    entry_new = _make_api_entry(_KEY_NEW, kid="new")
    client = _build_client([entry_old, entry_new])

    r = client.post(
        "/v1/recipes/demo:recommend",
        json=_VERB_BODIES["recommend"],
        headers={"X-API-Key": _KEY_NEITHER},
    )
    assert r.status_code == 401, (
        f"Unrecognised key must be rejected; got {r.status_code}: {r.text}"
    )
    assert r.json().get("code") == "INVALID_API_KEY"
