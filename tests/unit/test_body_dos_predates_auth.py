"""The request body is buffered and JSON-parsed *before* authentication.

FastAPI reads and JSON-parses the request body while it resolves the endpoint's
parameters (``fastapi/routing.py``: ``await request.body()`` / ``request.json()``
run ahead of ``solve_dependencies``), and the ``X-API-Key`` dependency is
resolved in that same phase.  So a request that is ultimately rejected with
``401`` has already had its body allocated and parsed, and an *unauthenticated*
caller can drive that allocation.  ``RECOTEM_MAX_BODY_BYTES`` bounds the raw
bytes but not the several-fold expansion of a JSON body into Python objects.

``docs/security.md`` documents this and tells operators to cap the body at the
proxy as well.  This test guards both:

* the runtime ordering (a no-key request with an invalid JSON body is answered
  ``422`` from body parsing, not ``401`` from auth — proving the body was parsed
  before the key was checked); and
* that the security doc keeps the corrected guidance (the pre-auth framing and a
  ``client_max_body_size`` directive in the recommended nginx config).

Reverting either the doc wording or the nginx directive fails this test; a future
change that moved auth ahead of body parsing would flip the ordering assertion,
which must then be re-examined together with the doc.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

_ROOT = Path(__file__).resolve().parents[2]
_SECURITY_DOC = _ROOT / "docs" / "security.md"


def _hash_key(plaintext: str) -> str:
    return hashlib.scrypt(
        plaintext.encode(), salt=b"recotem.api-key.v1", n=2, r=8, p=1, dklen=32
    ).hex()


def _client_with_auth() -> TestClient:
    registry = ModelRegistry()
    rec = MagicMock()
    rec._mapper = MagicMock()
    rec._mapper.item_id_to_index = {"i1": 0}
    registry.replace(
        "demo",
        ModelEntry(
            name="demo",
            recommender=rec,
            header={},
            kid="active",
            metadata_df=None,
            metadata_index=None,
            loaded=True,
            _loaded_marker=(None, "c" * 64),
            loaded_at_unix=1.0,
        ),
    )
    entry = ApiKeyEntry(kid="k1", sha256_hex=_hash_key("x" * 40))
    return TestClient(
        build_v1_app(registry, api_keys=[entry]), raise_server_exceptions=False
    )


# --- runtime ordering: body parse precedes the X-API-Key check ----------------


def test_invalid_body_without_key_is_422_not_401() -> None:
    """No key + malformed JSON is answered from body parsing, not from auth.

    If auth ran first, a request carrying no ``X-API-Key`` would be ``401``
    regardless of the body.  A ``422`` therefore proves the body was already
    read and JSON-parsed before authentication — i.e. an unauthenticated caller
    can drive the allocation the body cap bounds.
    """
    client = _client_with_auth()
    resp = client.post(
        "/v1/recipes/demo:recommend",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422, resp.text


def test_valid_body_without_key_is_401() -> None:
    """A well-formed body with no key still parses, then auth rejects it."""
    client = _client_with_auth()
    resp = client.post(
        "/v1/recipes/demo:recommend",
        content=b'{"user_id":"u1","limit":5}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "MISSING_API_KEY"


# --- doc guard: security.md keeps the corrected guidance ----------------------


def test_security_doc_states_allocation_precedes_auth() -> None:
    text = _SECURITY_DOC.read_text(encoding="utf-8")
    assert "precedes authentication" in text, (
        "docs/security.md must state that the request-body allocation precedes "
        "authentication (an unauthenticated caller can drive it)."
    )


def test_security_doc_nginx_config_caps_body_size() -> None:
    text = _SECURITY_DOC.read_text(encoding="utf-8")
    assert "client_max_body_size" in text, (
        "the recommended nginx config in docs/security.md must cap the request "
        "body at the proxy, before recotem buffers/parses it pre-auth."
    )


def test_security_doc_nginx_config_bounds_concurrency() -> None:
    """The ceiling is peak concurrency x body size, so a body cap is only half.

    Resident memory is reused rather than returned to the OS, so a long request
    sequence does not climb — but simultaneous requests do.  A rate limit alone
    does not bound that, which is why the recommended config also carries a
    concurrent-connection limit.
    """
    text = _SECURITY_DOC.read_text(encoding="utf-8")
    assert "limit_conn" in text, (
        "the recommended nginx config in docs/security.md must bound "
        "simultaneous in-flight requests: the pre-auth allocation ceiling is "
        "peak concurrency x body size, which a rate limit alone does not cap."
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
