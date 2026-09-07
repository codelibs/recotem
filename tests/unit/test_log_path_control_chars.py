"""The request path reaches log fields with its control characters escaped.

``request.url.path`` is attacker-controlled: a percent-encoded ``%1B`` in the
request target is unquoted by the ASGI server, so the ASGI ``scope["path"]``
carries a raw ESC byte.  Both log sites that carry that value render it as a
plain string field -- ``ConsoleRenderer`` uses ``str()``, not ``repr()``, for a
top-level string -- so an unescaped value reaches the terminal of an operator
tailing the log as live control sequences.

These tests assert on the *event dict* (via ``structlog.testing.capture_logs``)
rather than on rendered output, because the escaping must happen before the
renderer is chosen: the JSON renderer already escapes, the console renderer
does not, and only the console case is exposed.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest
import structlog
from fastapi import HTTPException

from recotem.config import ApiKeyEntry
from recotem.serving.auth import verify_api_key

# One ESC-based sequence that clears the screen and homes the cursor, plus a
# NUL: what an operator tailing the log would otherwise execute.
_HOSTILE = "/v1/recipes/\x1b[2J\x1b[H\x00evil:recommend"
_ESCAPED = "/v1/recipes/\\x1b[2J\\x1b[H\\x00evil:recommend"


def _make_request(path: str, api_key: str | None = None) -> MagicMock:
    request = MagicMock()
    request.url.path = path
    request.headers = {} if api_key is None else {"x-api-key": api_key}
    request.state = MagicMock()
    return request


def _entry(kid: str, plaintext: str) -> ApiKeyEntry:
    digest = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return ApiKeyEntry(kid=kid, sha256_hex=digest)


@pytest.mark.parametrize(
    ("api_key", "expected_event"),
    [
        (None, "auth_missing_header"),
        ("x" * 43, "auth_invalid_key"),
        ("x" * 8, "auth_short_key_rejected"),
        ("x" * 300, "auth_oversized_header"),
    ],
)
def test_auth_rejection_events_escape_control_chars_in_path(
    api_key: str | None, expected_event: str
) -> None:
    """Every pre-auth rejection log site escapes the path it reports.

    All four fire before any key matches, so they are reachable by an
    unauthenticated caller.
    """
    entries = [_entry("k1", "correct-key-value-that-is-long-enough")]
    request = _make_request(_HOSTILE, api_key)

    with structlog.testing.capture_logs() as logs:
        with pytest.raises(HTTPException):
            verify_api_key(request, entries)

    events = [e for e in logs if e["event"] == expected_event]
    assert events, (
        f"expected a {expected_event} event, got {[e['event'] for e in logs]}"
    )
    logged_path = events[0]["path"]
    assert "\x1b" not in logged_path, f"raw ESC reached the log field: {logged_path!r}"
    assert "\x00" not in logged_path, f"raw NUL reached the log field: {logged_path!r}"
    assert logged_path == _ESCAPED


def test_anonymous_bypass_events_escape_control_chars_in_path() -> None:
    """The no-keys bypass path logs the request path too, and escapes it."""
    request = _make_request(_HOSTILE)

    with structlog.testing.capture_logs() as logs:
        assert verify_api_key(request, []) == "anonymous"

    paths = [e["path"] for e in logs if "path" in e]
    assert paths, f"expected a bypass event carrying a path, got {logs}"
    for logged in paths:
        assert "\x1b" not in logged
        assert logged == _ESCAPED


def test_benign_path_is_logged_verbatim() -> None:
    """Control: a path with no control characters is not rewritten."""
    benign = "/v1/recipes/news_articles:recommend"
    request = _make_request(benign)

    with structlog.testing.capture_logs() as logs:
        with pytest.raises(HTTPException):
            verify_api_key(request, [_entry("k1", "correct-key-value-long-enough!!")])

    events = [e for e in logs if e["event"] == "auth_missing_header"]
    assert events[0]["path"] == benign


# ---------------------------------------------------------------------------
# End-to-end: the bytes really do arrive in scope["path"]
# ---------------------------------------------------------------------------


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> object:
    from fastapi.testclient import TestClient

    from recotem.config import ServeConfig
    from recotem.serving.app import create_app

    plaintext = "e2e-api-key-value-that-is-long-enough"
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
    return TestClient(create_app(cfg)), plaintext


def test_percent_encoded_escape_reaches_scope_path_and_is_escaped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """A ``%1B`` in the request target is unquoted into ``scope["path"]``.

    Sent unauthenticated (``auth_missing_header``) and then authenticated
    (``validation_failed``, since the name fails the recipe-name pattern), so
    both log sites are exercised over the real ASGI path.
    """
    client, plaintext = _client(monkeypatch, tmp_path)
    target = "/v1/recipes/%1B%5B2J%1B%5BHevil:recommend"

    with client:
        with structlog.testing.capture_logs() as logs:
            assert (
                client.post(target, json={"user_id": "u", "limit": 3}).status_code
                == 401
            )
        unauth = [e for e in logs if e["event"] == "auth_missing_header"]

        with structlog.testing.capture_logs() as logs:
            resp = client.post(
                target,
                json={"user_id": "u", "limit": 3},
                headers={"X-API-Key": plaintext},
            )
            assert resp.status_code == 422
        auth = [e for e in logs if e["event"] == "validation_failed"]

    assert unauth, "the unauthenticated request produced no auth_missing_header event"
    assert auth, "the authenticated request produced no validation_failed event"
    for event in (*unauth, *auth):
        assert "\x1b" not in event["path"], (
            f"raw ESC in {event['event']}: {event['path']!r}"
        )
        # The bytes did arrive -- otherwise this asserts nothing.
        assert "\\x1b" in event["path"], (
            f"{event['event']} did not receive the escape bytes at all: {event['path']!r}"
        )
