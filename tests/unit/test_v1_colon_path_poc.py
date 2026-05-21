"""Temporary POC: confirm FastAPI accepts AIP-136 colon-verb paths.

Deleted in Task 13 once the real v1 endpoints replace it.
"""

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


def test_colon_path_routes_and_appears_in_openapi():
    router = APIRouter()

    @router.post("/recipes/{name}:recommend")
    def _recommend(name: str) -> dict[str, str]:
        return {"name": name, "verb": "recommend"}

    @router.post("/recipes/{name}:recommend-related")
    def _related(name: str) -> dict[str, str]:
        return {"name": name, "verb": "recommend-related"}

    app = FastAPI()
    app.include_router(router, prefix="/v1")
    client = TestClient(app)

    r1 = client.post("/v1/recipes/demo:recommend")
    assert r1.status_code == 200
    assert r1.json() == {"name": "demo", "verb": "recommend"}

    r2 = client.post("/v1/recipes/demo:recommend-related")
    assert r2.status_code == 200
    assert r2.json() == {"name": "demo", "verb": "recommend-related"}

    spec = client.get("/openapi.json").json()
    assert "/v1/recipes/{name}:recommend" in spec["paths"]
    assert "/v1/recipes/{name}:recommend-related" in spec["paths"]
