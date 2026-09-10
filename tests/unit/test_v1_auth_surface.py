"""Which ``/v1`` routes an unauthenticated caller can reach, pinned as a whole.

The split is not uniform: the three kubelet probes are deliberately open and
everything else takes ``Depends(_require_auth)``.  Individual tests already
pin single routes (``/v1/health/details`` 401, ``/v1/metrics`` 401), but
nothing pinned the *set*, and the missing half is the one that keeps getting
restated wrongly -- "``Depends(_require_auth)`` like every other ``/v1``
route" was carried from one file to another without anyone being able to run
it.

So assert the whole surface in one table.  Adding a route means adding a row,
which is the point: a new endpoint that quietly lands on the open side fails
here rather than in someone's threat model.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelRegistry
from tests.conftest import build_v1_app

_PLAINTEXT = "auth_surface_key_32_bytes_padded!"


def _entry(kid: str = "k1") -> ApiKeyEntry:
    sha256_hex = hashlib.scrypt(
        _PLAINTEXT.encode(),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return ApiKeyEntry(kid=kid, sha256_hex=sha256_hex)


def _client() -> TestClient:
    return TestClient(build_v1_app(ModelRegistry(), api_keys=[_entry()]))


# (method, path, body, reachable-without-a-key, why)
_OPEN = [
    ("GET", "/v1/health", None, "a kubelet reads it as the overall probe"),
    ("GET", "/v1/health/live", None, "livenessProbe, no key available"),
    ("GET", "/v1/health/ready", None, "readinessProbe, no key available"),
]

_CLOSED = [
    ("GET", "/v1/health/details", None),
    ("GET", "/v1/recipes", None),
    ("POST", "/v1/recipes/probe:recommend", {"user_id": "u1"}),
    ("POST", "/v1/recipes/probe:recommend-related", {"seed_items": ["i1"]}),
    ("POST", "/v1/recipes/probe:batch-recommend", {"requests": []}),
    ("POST", "/v1/recipes/probe:batch-recommend-related", {"requests": []}),
]


@pytest.mark.parametrize(("method", "path", "body", "why"), _OPEN)
def test_probe_routes_are_reachable_without_a_key(method, path, body, why) -> None:
    r = _client().request(method, path, json=body)
    assert r.status_code != 401, (
        f"{method} {path} now demands an API key, but {why}. A probe that "
        "401s is reported by the kubelet as an unhealthy container, so this "
        "change restarts every pod rather than protecting anything."
    )


@pytest.mark.parametrize(("method", "path", "body"), _CLOSED)
def test_every_other_v1_route_demands_a_key(method, path, body) -> None:
    r = _client().request(method, path, json=body)
    assert r.status_code == 401, (
        f"{method} {path} answered {r.status_code} without an API key. Only "
        "the three kubelet probes are open; everything else on /v1 must take "
        "Depends(_require_auth)."
    )


@pytest.mark.parametrize(("method", "path", "body"), _CLOSED)
def test_the_closed_routes_stop_being_401_with_a_key(method, path, body) -> None:
    """Positive control: the 401 above is the auth gate, not a missing route."""
    r = _client().request(method, path, json=body, headers={"X-API-Key": _PLAINTEXT})
    assert r.status_code != 401, (
        f"{method} {path} answered 401 with a valid key, so the 401 in the "
        "test above proves nothing about authentication."
    )


def test_metrics_is_on_the_closed_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/v1/metrics`` is separate because it 404s until it is enabled.

    That 404 is why it belongs in this file at all: an operator whose probes
    answer without a key reasonably expects the scrape to as well, and the
    endpoint they get instead is a 401 that reads like a wrong key.
    """
    pytest.importorskip("prometheus_client")
    from recotem.serving import metrics as _m

    monkeypatch.setattr(_m, "metrics_enabled", lambda: True)
    client = _client()
    assert client.get("/v1/metrics").status_code == 401
    assert client.get("/v1/metrics", headers={"X-API-Key": _PLAINTEXT}).status_code == (
        200
    )
