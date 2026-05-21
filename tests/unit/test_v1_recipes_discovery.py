# tests/unit/test_v1_recipes_discovery.py
"""GET /v1/recipes and GET /v1/recipes/{name} discovery endpoints."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app


def _make_api_entry(plaintext: str, kid: str = "api-key") -> ApiKeyEntry:
    digest = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return ApiKeyEntry(kid=kid, sha256_hex=digest)


def _client_with_entries(
    entries: list[ModelEntry],
    api_keys: list[ApiKeyEntry] | None = None,
) -> TestClient:
    registry = ModelRegistry()
    for e in entries:
        registry.replace(e.name, e)
    return TestClient(build_v1_app(registry, api_keys=api_keys or []))


def _stub(name: str) -> ModelEntry:
    return ModelEntry(
        name=name,
        recommender=object(),
        header={},
        kid="t",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc"),
        loaded_at_unix=1747800000.0,
    )


def test_recipes_list_returns_summaries():
    r = _client_with_entries([_stub("a"), _stub("b")]).get("/v1/recipes")
    assert r.status_code == 200
    body = r.json()
    names = {x["name"] for x in body["recipes"]}
    assert names == {"a", "b"}
    a = next(x for x in body["recipes"] if x["name"] == "a")
    assert a["model_version"] == "sha256:abc"
    assert a["kind"] == "user-item"
    assert "recommend" in a["supported_verbs"]


def test_recipe_detail_returns_404_for_unknown():
    r = _client_with_entries([_stub("a")]).get("/v1/recipes/unknown")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "RECIPE_NOT_FOUND"
    assert isinstance(body["detail"], str)


def test_recipe_detail_returns_503_for_stub_not_loaded():
    unloaded = ModelEntry(
        name="broken",
        recommender=None,
        header={},
        kid="",
        loaded=False,
    )
    r = _client_with_entries([unloaded]).get("/v1/recipes/broken")
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "RECIPE_UNAVAILABLE"
    assert isinstance(body["detail"], str)


def test_recipe_detail_returns_full_summary_for_known():
    r = _client_with_entries([_stub("a")]).get("/v1/recipes/a")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "a"
    assert body["model_version"] == "sha256:abc"
    # algorithms / best_algorithm / config_digest may be empty for the
    # stub but the keys MUST exist (contract).
    assert "algorithms" in body
    assert "best_algorithm" in body
    assert "config_digest" in body


# ---------------------------------------------------------------------------
# B. Discovery auth boundary (I3)
# ---------------------------------------------------------------------------


_VALID_PLAINTEXT = "discovery_test_api_key_32_bytes!"


def test_list_recipes_requires_auth() -> None:
    api_entry = _make_api_entry(_VALID_PLAINTEXT)
    r = _client_with_entries([_stub("a")], api_keys=[api_entry]).get("/v1/recipes")
    assert r.status_code == 401


def test_list_recipes_rejects_wrong_key() -> None:
    api_entry = _make_api_entry(_VALID_PLAINTEXT)
    r = _client_with_entries([_stub("a")], api_keys=[api_entry]).get(
        "/v1/recipes",
        headers={"X-API-Key": "wrong_key_value_32_bytes_padding!"},
    )
    assert r.status_code == 401


def test_list_recipes_accepts_valid_key() -> None:
    api_entry = _make_api_entry(_VALID_PLAINTEXT)
    r = _client_with_entries([_stub("a")], api_keys=[api_entry]).get(
        "/v1/recipes",
        headers={"X-API-Key": _VALID_PLAINTEXT},
    )
    assert r.status_code == 200


def test_recipe_detail_requires_auth() -> None:
    api_entry = _make_api_entry(_VALID_PLAINTEXT)
    r = _client_with_entries([_stub("a")], api_keys=[api_entry]).get("/v1/recipes/a")
    assert r.status_code == 401


def test_recipe_detail_rejects_wrong_key() -> None:
    api_entry = _make_api_entry(_VALID_PLAINTEXT)
    r = _client_with_entries([_stub("a")], api_keys=[api_entry]).get(
        "/v1/recipes/a",
        headers={"X-API-Key": "wrong_key_value_32_bytes_padding!"},
    )
    assert r.status_code == 401


def test_recipe_detail_accepts_valid_key() -> None:
    api_entry = _make_api_entry(_VALID_PLAINTEXT)
    r = _client_with_entries([_stub("a")], api_keys=[api_entry]).get(
        "/v1/recipes/a",
        headers={"X-API-Key": _VALID_PLAINTEXT},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# I. New detail fields from artifact header
# ---------------------------------------------------------------------------

_HEADER_FIELDS = {
    "trained_at": "2026-01-01T00:00:00Z",
    "best_class": "TopPopRecommender",
    "best_params": {"alpha": 0.1},
    "best_score": 0.42,
    "metric": "ndcg",
    "cutoff": 10,
    "tuning": {"n_trials": 5},
    "data_stats": {"n_users": 100, "n_items": 50},
    "recotem_version": "1.0.0",
    "irspack_version": "0.3.0",
    "recipe_hash": "aabbcc",
}


def _stub_with_header(name: str, header: dict) -> ModelEntry:
    return ModelEntry(
        name=name,
        recommender=object(),
        header=header,
        kid="t",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc"),
        loaded_at_unix=1747800000.0,
    )


def test_recipe_detail_exposes_artifact_header_fields() -> None:
    entry = _stub_with_header("myrecipe", _HEADER_FIELDS)
    r = _client_with_entries([entry]).get("/v1/recipes/myrecipe")
    assert r.status_code == 200
    body = r.json()
    for field_name, expected in _HEADER_FIELDS.items():
        assert body[field_name] == expected, (
            f"Field {field_name!r}: expected {expected!r}, got {body[field_name]!r}"
        )


def test_recipe_detail_tolerates_missing_header_fields() -> None:
    entry = _stub_with_header("emptyheader", {})
    r = _client_with_entries([entry]).get("/v1/recipes/emptyheader")
    assert r.status_code == 200
    body = r.json()
    for field_name in _HEADER_FIELDS:
        assert body[field_name] is None, (
            f"Field {field_name!r} should be null when header omits it; "
            f"got {body[field_name]!r}"
        )
