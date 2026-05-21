# tests/unit/test_v1_health_metrics.py
"""Verify /v1/health, /v1/health/details, and /v1/metrics behave like
their legacy counterparts but mounted under /v1.
"""

import hashlib

from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelRegistry
from tests.conftest import build_v1_app


def _client(api_keys: list[ApiKeyEntry] | None = None) -> TestClient:
    registry = ModelRegistry()
    return TestClient(build_v1_app(registry, api_keys=api_keys or []))


def _entry() -> ApiKeyEntry:
    plaintext = "api_key_32_bytes_exactly_here!!!"
    sha256_hex = hashlib.scrypt(
        plaintext.encode(),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return ApiKeyEntry(kid="k1", sha256_hex=sha256_hex)


def test_health_returns_ok_with_empty_registry():
    r = _client().get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["total"] == 0
    assert body["loaded"] == 0


def test_health_details_requires_auth():
    r = _client(api_keys=[_entry()]).get("/v1/health/details")
    assert r.status_code == 401
