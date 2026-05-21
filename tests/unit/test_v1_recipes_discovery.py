# tests/unit/test_v1_recipes_discovery.py
"""GET /v1/recipes and GET /v1/recipes/{name} discovery endpoints."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.v1_router import make_v1_router


def _client_with_entries(entries: list[ModelEntry]) -> TestClient:
    registry = ModelRegistry()
    for e in entries:
        registry.replace(e.name, e)
    app = FastAPI()
    app.include_router(make_v1_router(registry, []), prefix="/v1")
    return TestClient(app)


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
    assert r.json()["detail"]["code"] == "RECIPE_NOT_FOUND"


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
    assert r.json()["detail"]["code"] == "RECIPE_UNAVAILABLE"


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
