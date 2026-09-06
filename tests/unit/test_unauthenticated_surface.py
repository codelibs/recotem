"""The set of endpoints that answer without an API key is pinned.

``/v1/health``, ``/v1/health/live`` and ``/v1/health/ready`` are
unauthenticated on purpose -- a kubelet probe carries no key.  Nothing else
may join them by accident.

The set is derived from the running app rather than from a hard-coded list:
every path in the OpenAPI schema is called without a key, and anything that
does not answer 401 is an unauthenticated endpoint.  A fourth one added
tomorrow fails this test on the day it lands.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recotem.config import ServeConfig

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def unauthenticated_paths(tmp_path_factory: pytest.TempPathFactory) -> set[str]:
    """Paths that answer something other than 401 with no ``X-API-Key``."""
    from recotem.serving.app import create_app

    tmp = tmp_path_factory.mktemp("unauth")
    (tmp / "recipes").mkdir()
    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    cfg.recipes_dir = str(tmp / "recipes")  # type: ignore[attr-defined]
    cfg.env = "production"
    cfg.insecure_no_auth = False
    # A real key ring, so the auth dependency is armed rather than bypassed.
    cfg.api_keys = ["probe:sha256:" + "bb" * 32]  # type: ignore[attr-defined]
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1"]

    app = create_app(cfg)
    client = TestClient(app)
    open_paths: set[str] = set()
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            url = path.replace("{name}", "no-such-recipe")
            response = client.request(method.upper(), url, json={})
            if response.status_code != 401:
                open_paths.add(path)
    return open_paths


def test_the_unauthenticated_surface_is_exactly_the_three_probe_endpoints(
    unauthenticated_paths: set[str],
) -> None:
    """Pins the surface itself, so a widening is a deliberate act."""
    assert unauthenticated_paths == {
        "/v1/health",
        "/v1/health/ready",
        "/v1/health/live",
    }, (
        "the set of endpoints reachable without an X-API-Key changed. If that "
        "is intended, say so explicitly by updating this test."
    )
