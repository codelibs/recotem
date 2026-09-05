"""The docs must name every endpoint that answers without an API key.

``docs/api-reference.md`` said "All endpoints except ``/v1/health`` require the
``X-API-Key`` header".  That was true until #219 added ``/v1/health/live`` and
``/v1/health/ready``, which are unauthenticated for the same reason the third
one is -- a kubelet probe carries no key.  The same PR documented both
endpoints 150 lines further down the same file, so the file contradicted
itself, and ``docs/security.md``'s trust-boundary diagram -- the enumeration of
what an unauthenticated caller can reach -- kept listing one path.

The check derives the set from the running app rather than from a hard-coded
list: every path in the OpenAPI schema is called without a key, and anything
that does not answer 401 is an unauthenticated endpoint.  A fourth one added
tomorrow fails this test on the day it lands, whether or not anyone remembers
these documents exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recotem.config import ServeConfig

_ROOT = Path(__file__).resolve().parents[2]
_API_REFERENCE = _ROOT / "docs" / "api-reference.md"
_SECURITY = _ROOT / "docs" / "security.md"

_PATH_RE = re.compile(r"/v1/health(?:/[a-z]+)?")


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
        "is intended, update docs/api-reference.md's Authentication section "
        "and docs/security.md's trust-boundary diagram in the same commit."
    )


def test_api_reference_authentication_section_names_them_all(
    unauthenticated_paths: set[str],
) -> None:
    """The authoritative API reference must not undercount the open surface."""
    text = _API_REFERENCE.read_text(encoding="utf-8")
    start = text.index("## Authentication")
    section = text[start : text.index("\n## ", start + 1)]
    # Only the bullet list, so the surrounding prose may name the endpoints
    # that *do* require a key without being read as a claim about this set.
    named = {
        path
        for line in section.splitlines()
        if line.lstrip().startswith("- ")
        for path in _PATH_RE.findall(line)
    }
    missing = unauthenticated_paths - named
    assert not missing, (
        f"docs/api-reference.md's Authentication section does not name "
        f"{sorted(missing)}, which answer without an X-API-Key. A reader "
        "builds their gateway or ingress rules from this paragraph."
    )
    overclaimed = named - unauthenticated_paths
    assert not overclaimed, (
        f"the Authentication section lists {sorted(overclaimed)} as open, but "
        "they require a key -- probes pointed there would get 401"
    )


def test_security_trust_boundary_diagram_names_them_all(
    unauthenticated_paths: set[str],
) -> None:
    """The diagram is the enumeration of what an unauthenticated caller reaches."""
    text = _SECURITY.read_text(encoding="utf-8")
    start = text.index("## Trust boundaries")
    diagram = text[start : text.index("```", text.index("```", start) + 3)]
    named = set(_PATH_RE.findall(diagram))
    missing = unauthenticated_paths - named
    assert not missing, (
        f"docs/security.md's trust-boundary diagram omits {sorted(missing)}, "
        "which are reachable without an X-API-Key"
    )
