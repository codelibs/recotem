"""The 500 handler escapes the request path it logs, and nothing pinned that.

#364 escaped ``request.url.path`` at eight log sites.  Seven are covered by
``tests/unit/test_log_path_control_chars.py``; the ``unhandled_500`` handler in
``serving/app.py`` is not.  Measured on b554aa0 by deleting
``escape_control_chars`` from that one call site and running ``tests/unit`` +
``tests/integration``: 3050 passed.  The one site whose log line is written
with ``logger.exception`` -- the one an operator is most likely to be reading
live -- was the one a refactor could drop for free.

Scope, stated rather than implied: no request routed by today's app reaches
this handler with a control character in the path.  The four verbs bound the
recipe name to ``^[A-Za-z0-9_-]{1,64}$``, so a hostile target 404s or 422s
before any handler body runs.  The escape here is defence in depth for the
generic catch-all -- middleware added later (a body-size guard, a tracing
shim, an auth proxy) raises for whatever target the caller sent, and this
handler logs it.  The test therefore drives the handler through a route
registered for the test, which is what makes the assertion about the handler
rather than about the router.
"""

from __future__ import annotations

import hashlib

import pytest
import structlog
from fastapi.testclient import TestClient

from recotem.config import ServeConfig
from recotem.serving.app import create_app

_HOSTILE_SUFFIX = "\x1b[2J\x1b[Hevil"


def _app(monkeypatch: pytest.MonkeyPatch, tmp_path: object):
    plaintext = "unhandled-500-api-key-long-enough"
    digest = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    monkeypatch.setenv("RECOTEM_API_KEYS", f"k1:sha256:{digest}")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", "s1:" + "cd" * 32)
    monkeypatch.setenv("RECOTEM_ALLOWED_HOSTS", "testserver")
    cfg = ServeConfig.from_env()
    cfg.recipes_dir = str(tmp_path)
    return create_app(cfg)


def test_unhandled_500_escapes_control_chars_in_the_logged_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    app = _app(monkeypatch, tmp_path)

    @app.get("/boom/{tail:path}")
    def _boom(tail: str) -> None:  # pragma: no cover - body raises
        raise RuntimeError("probe")

    client = TestClient(app, raise_server_exceptions=False)
    with client:
        with structlog.testing.capture_logs() as logs:
            resp = client.get("/boom/%1B%5B2J%1B%5BHevil")
    assert resp.status_code == 500

    events = [e for e in logs if e["event"] == "unhandled_500"]
    assert events, f"expected an unhandled_500 event, got {[e['event'] for e in logs]}"
    logged = events[0]["path"]
    assert "\x1b" not in logged, f"raw ESC reached the log field: {logged!r}"
    # The bytes did arrive, so the assertion above is not vacuous.
    assert "\\x1b" in logged, (
        f"the handler never saw the escape bytes at all: {logged!r}"
    )


def test_unhandled_500_logs_a_benign_path_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Control: the escaping does not rewrite an ordinary path."""
    app = _app(monkeypatch, tmp_path)

    @app.get("/boom/{tail:path}")
    def _boom(tail: str) -> None:  # pragma: no cover - body raises
        raise RuntimeError("probe")

    client = TestClient(app, raise_server_exceptions=False)
    with client:
        with structlog.testing.capture_logs() as logs:
            client.get("/boom/ordinary-path")

    events = [e for e in logs if e["event"] == "unhandled_500"]
    assert events and events[0]["path"] == "/boom/ordinary-path"
