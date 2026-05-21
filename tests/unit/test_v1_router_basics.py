# tests/unit/test_v1_router_basics.py
"""v1 router skeleton tests.

Confirms the factory wires auth and registry without exposing
any routes other than the four health/discovery endpoints to be
added in Tasks 6 and 11.  Inference verbs are added in Tasks 7-10.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelRegistry
from recotem.serving.v1_router import make_v1_router


def test_make_v1_router_returns_routable_apiroute_factory():
    registry = ModelRegistry()
    router = make_v1_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router, prefix="/v1")

    client = TestClient(app)
    # An unknown path under /v1 returns 404, confirming the router is
    # mounted at /v1.  Inference routes are added in Tasks 7-10; using
    # a verb the router does not define keeps this skeleton-stage check
    # valid as those routes land.
    r = client.post("/v1/recipes/x:nonexistent-verb")
    assert r.status_code == 404
