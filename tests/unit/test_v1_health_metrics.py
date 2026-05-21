# tests/unit/test_v1_health_metrics.py
"""Verify /v1/health, /v1/health/details, and /v1/metrics behave like
their legacy counterparts but mounted under /v1.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelRegistry
from tests.conftest import build_v1_app


def _client(api_keys: list[ApiKeyEntry] | None = None) -> TestClient:
    registry = ModelRegistry()
    return TestClient(build_v1_app(registry, api_keys=api_keys or []))


def _make_api_entry(plaintext: str, kid: str = "k1") -> ApiKeyEntry:
    sha256_hex = hashlib.scrypt(
        plaintext.encode(),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return ApiKeyEntry(kid=kid, sha256_hex=sha256_hex)


def _entry() -> ApiKeyEntry:
    return _make_api_entry("api_key_32_bytes_exactly_here!!!")


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


# ---------------------------------------------------------------------------
# A. /v1/metrics endpoint gating (I4)
# ---------------------------------------------------------------------------


def test_metrics_endpoint_404_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RECOTEM_METRICS_ENABLED", raising=False)
    from recotem.serving import metrics as _m

    monkeypatch.setattr(_m, "metrics_enabled", lambda: False)
    client = _client(api_keys=[_entry()])
    r = client.get("/v1/metrics")
    assert r.status_code == 404


def test_metrics_endpoint_404_when_env_falsy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "0")
    from recotem.serving import metrics as _m

    monkeypatch.setattr(_m, "metrics_enabled", lambda: False)
    client = _client(api_keys=[_entry()])
    r = client.get("/v1/metrics")
    assert r.status_code == 404


def test_metrics_endpoint_requires_auth_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")
    from recotem.serving import metrics as _m

    monkeypatch.setattr(_m, "metrics_enabled", lambda: True)
    plaintext = "metrics_test_key_32_bytes_padded!"
    api_entry = _make_api_entry(plaintext)
    client = _client(api_keys=[api_entry])
    r = client.get("/v1/metrics")
    assert r.status_code == 401


def test_metrics_endpoint_200_with_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")
    from recotem.serving import metrics as _m

    monkeypatch.setattr(_m, "metrics_enabled", lambda: True)
    plaintext = "metrics_test_key_32_bytes_padded!"
    api_entry = _make_api_entry(plaintext)
    client = _client(api_keys=[api_entry])
    r = client.get("/v1/metrics", headers={"X-API-Key": plaintext})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "# HELP" in r.text or "# TYPE" in r.text
