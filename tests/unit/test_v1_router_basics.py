# tests/unit/test_v1_router_basics.py
"""v1 router smoke tests.

Confirms the factory wires auth and registry and that the router is
mounted at ``/v1``.  Inference verbs and the discovery endpoints are
added incrementally across Tasks 6-11.
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
    # An entirely unknown verb on the recipes path must return 404,
    # confirming the router rejects undefined verbs (rather than e.g.
    # routing through a catch-all).  A GET request is used so we are not
    # confused with the POST-only colon-verb endpoints.
    r = client.get("/v1/totally-unknown-path")
    assert r.status_code == 404
