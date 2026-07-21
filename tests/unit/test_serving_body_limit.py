"""Tests for BodySizeLimitMiddleware — the serve-side request body cap.

Covers both enforcement points:
- a declared Content-Length above the cap is rejected before the body is read;
- a chunked/streamed body with no Content-Length is rejected on a running count.
A normal-sized body must pass the gate untouched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recotem.config import ServeConfig
from recotem.serving.app import create_app

_CAP = 1024 * 1024  # 1 MiB (the clamp minimum), keeps the oversized body small.


def _app_with_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_MAX_BODY_BYTES", str(_CAP))
    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]
    return create_app(cfg)


def test_oversized_content_length_returns_413(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app_with_cap(tmp_path, monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    # A bytes body sets an explicit Content-Length > cap → rejected outright.
    resp = client.post(
        "/v1/recipes/demo:recommend",
        content=b"a" * (_CAP + 1),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413
    body = resp.json()
    assert body["code"] == "PAYLOAD_TOO_LARGE"
    assert "detail" in body


def test_oversized_chunked_body_without_content_length_returns_413(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app_with_cap(tmp_path, monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    def _gen() -> Iterator[bytes]:
        # 16 chunks of 128 KiB = 2 MiB, streamed with Transfer-Encoding: chunked
        # (no Content-Length), so only the running-count guard can catch it.
        for _ in range(16):
            yield b"a" * (128 * 1024)

    resp = client.post(
        "/v1/recipes/demo:recommend",
        content=_gen(),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_normal_body_passes_the_size_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app_with_cap(tmp_path, monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    # Well under the cap: the gate must let it through to routing, which 404s
    # because no recipe named "demo" is loaded — proving it was NOT a 413.
    resp = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert resp.status_code != 413
    assert resp.status_code == 404
    assert resp.json()["code"] == "RECIPE_NOT_FOUND"


def test_413_carries_request_id_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The body cap sits inside RequestIDMiddleware, so its 413 still carries
    an X-Request-ID for correlation."""
    app = _app_with_cap(tmp_path, monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/v1/recipes/demo:recommend",
        content=b"a" * (_CAP + 1),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.headers.get("x-request-id")
