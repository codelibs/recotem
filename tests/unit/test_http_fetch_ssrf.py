"""Unit tests for SSRF guards in recotem._http_fetch.

Tests:
- assert_host_public rejects loopback, link-local, RFC1918, IPv6 loopback
- assert_host_public rejects unresolvable host
- assert_host_public allows public IP
- assert_host_public is a no-op when allow_private=True
- fetch_http_bytes refuses loopback when RECOTEM_HTTP_ALLOW_PRIVATE=0
- fetch_http_bytes refuses redirect to private address
- fetch_http_bytes raises on too many redirects
- fetch_http_bytes raises on timeout

Important: conftest autouse fixture sets RECOTEM_HTTP_ALLOW_PRIVATE=1.
Tests that exercise SSRF guards must set it to "0" or delete it.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from recotem._http_fetch import HttpFetchError, assert_host_public, fetch_http_bytes

pytest_plugins = ("pytest_httpserver",)


# ---------------------------------------------------------------------------
# C1. assert_host_public rejects loopback
# ---------------------------------------------------------------------------


def test_assert_host_public_rejects_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """127.0.0.1 is a loopback address and must be refused when allow_private=False."""
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")
    with pytest.raises(HttpFetchError, match="private/internal address"):
        assert_host_public("http://127.0.0.1/x", allow_private=False)


# ---------------------------------------------------------------------------
# C2. assert_host_public rejects link-local IMDS (169.254.169.254)
# ---------------------------------------------------------------------------


def test_assert_host_public_rejects_link_local_imdsv1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """169.254.169.254 is the cloud metadata link-local address; must be refused."""
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")
    with pytest.raises(HttpFetchError, match="private/internal address"):
        assert_host_public(
            "http://169.254.169.254/latest/meta-data/", allow_private=False
        )


# ---------------------------------------------------------------------------
# C3. assert_host_public rejects RFC1918 addresses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "192.168.1.1",
        "10.0.0.1",
        "172.16.0.1",
    ],
)
def test_assert_host_public_rejects_rfc1918(
    ip: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC1918 private addresses must be rejected when allow_private=False."""
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")
    with pytest.raises(HttpFetchError, match="private/internal address"):
        assert_host_public(f"http://{ip}/path", allow_private=False)


# ---------------------------------------------------------------------------
# C4. assert_host_public rejects IPv6 loopback
# ---------------------------------------------------------------------------


def test_assert_host_public_rejects_ipv6_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IPv6 ::1 (loopback) must be refused when allow_private=False."""
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")
    with pytest.raises(HttpFetchError, match="private/internal address"):
        assert_host_public("http://[::1]/x", allow_private=False)


# ---------------------------------------------------------------------------
# C5. assert_host_public rejects unresolvable host
# ---------------------------------------------------------------------------


def test_assert_host_public_rejects_unresolvable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname that does not resolve must be rejected (prefer clear refusal
    over implicitly-safe behaviour against a poisoned resolver).
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")
    with pytest.raises(HttpFetchError, match="does not resolve"):
        assert_host_public(
            "http://thishostshouldnotexist.invalid/", allow_private=False
        )


# ---------------------------------------------------------------------------
# C6. assert_host_public allows public IP
# ---------------------------------------------------------------------------


def test_assert_host_public_allows_public_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public IP address (e.g. 93.184.216.34 — example.com) must pass the check.

    We stub getaddrinfo so the test does not depend on DNS being reachable and
    the IP not changing.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")

    public_ip = "93.184.216.34"

    def _stub_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, None, None, None, (public_ip, 0))]

    with patch("socket.getaddrinfo", side_effect=_stub_getaddrinfo):
        # Should not raise
        assert_host_public(f"http://{public_ip}/", allow_private=False)


# ---------------------------------------------------------------------------
# C7. assert_host_public is no-op when allow_private=True
# ---------------------------------------------------------------------------


def test_assert_host_public_no_op_when_allow_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When allow_private=True the check is bypassed entirely."""
    # No need to change RECOTEM_HTTP_ALLOW_PRIVATE; we pass directly
    assert_host_public("http://127.0.0.1/", allow_private=True)  # must not raise


# ---------------------------------------------------------------------------
# C8. fetch_http_bytes rejects loopback when RECOTEM_HTTP_ALLOW_PRIVATE=0
# ---------------------------------------------------------------------------


def test_fetch_http_bytes_rejects_loopback_when_disallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_http_bytes must refuse a loopback URL before connecting.

    We spin up a real HTTP server on 127.0.0.1 and verify that the SSRF
    guard fires before the handler is ever invoked.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")

    handler_called = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            handler_called.append(True)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"should not reach here")

        def log_message(self, *args):  # suppress request logging
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with pytest.raises(HttpFetchError, match="private/internal"):
            fetch_http_bytes(
                f"http://127.0.0.1:{port}/",
                timeout=5,
                max_bytes=1024,
                allow_private=False,
            )
        assert not handler_called, "SSRF guard must fire before reaching the server"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


# ---------------------------------------------------------------------------
# C9. fetch_http_bytes rejects redirect to private address
# ---------------------------------------------------------------------------


def test_fetch_http_bytes_rejects_redirect_to_private(
    monkeypatch: pytest.MonkeyPatch, httpserver
) -> None:
    """A 302 redirect from a public-appearing host to 127.0.0.1 must be refused.

    We stub _resolve_host_addresses so the first URL is treated as public, but
    the redirect target (127.0.0.1) resolves to a loopback address.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")

    # Set up the server to issue a 302 to 127.0.0.1
    private_redirect = f"http://127.0.0.1:{httpserver.port}/private"
    httpserver.expect_request("/public").respond_with_data(
        b"",
        status=302,
        headers={"Location": private_redirect},
    )

    public_url = httpserver.url_for("/public")
    # httpserver binds to 127.0.0.1. We make _resolve_host_addresses return a
    # public IP for the *first* check (the httpserver host), but let the real
    # resolution handle the redirect (which goes back to 127.0.0.1).
    # The key insight: the redirect URL's host is 127.0.0.1 literally, so the
    # real ipaddress.ip_address call will correctly classify it as loopback.

    original_resolve = None
    import recotem._http_fetch as _mod

    original_resolve = _mod._resolve_host_addresses

    call_count = [0]

    def _patched_resolve(host: str):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: httpserver host — pretend it's public
            return [ipaddress.ip_address("93.184.216.34")]
        # Subsequent calls: real resolution (127.0.0.1 → loopback)
        return original_resolve(host)

    with patch.object(_mod, "_resolve_host_addresses", side_effect=_patched_resolve):
        with pytest.raises(HttpFetchError, match="private/internal"):
            fetch_http_bytes(
                public_url,
                timeout=5,
                max_bytes=65536,
                allow_private=False,
            )


# ---------------------------------------------------------------------------
# C10. fetch_http_bytes raises on too many redirects
# ---------------------------------------------------------------------------


def test_fetch_http_bytes_max_redirects_exceeded(httpserver) -> None:
    """A chain of 6+ distinct redirects triggers the Too many redirects error."""
    # Build 6 unique paths, each redirecting to the next
    paths = [f"/r{i}" for i in range(7)]  # 7 paths = 6 hops from /r0 → /r6

    for i in range(len(paths) - 1):
        httpserver.expect_request(paths[i]).respond_with_data(
            b"",
            status=302,
            headers={"Location": httpserver.url_for(paths[i + 1])},
        )
    # Final path just returns 200 so we know the chain ends there
    httpserver.expect_request(paths[-1]).respond_with_data(b"ok", status=200)

    with pytest.raises(HttpFetchError, match="Too many redirects"):
        fetch_http_bytes(
            httpserver.url_for(paths[0]),
            timeout=10,
            max_bytes=65536,
            allow_private=True,  # conftest sets ALLOW_PRIVATE=1 anyway; be explicit
        )


# ---------------------------------------------------------------------------
# C11. fetch_http_bytes timeout actually fires
# ---------------------------------------------------------------------------


def test_fetch_http_bytes_timeout_actually_fires() -> None:
    """A server that sleeps before responding must trigger the timeout error.

    Uses a raw threading HTTPServer with a 3-second sleep handler and a
    1-second client timeout.  The fetch must raise within ~1.5 seconds —
    if it ever blocked waiting for the full sleep, the wall time alone
    catches the regression because the assertion below bounds elapsed time.
    """

    class _SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(3)  # exceed timeout=1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"too late")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _SlowHandler)
    port = server.server_address[1]

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    start = time.monotonic()
    try:
        with pytest.raises(HttpFetchError, match="URL error|timed out|timeout"):
            fetch_http_bytes(
                f"http://127.0.0.1:{port}/",
                timeout=1,
                max_bytes=65536,
                allow_private=True,
            )
        elapsed = time.monotonic() - start
        # Belt-and-braces: the timeout must fire well before the server
        # would have responded (3 s).  Allow some slack for thread startup.
        assert elapsed < 2.5, f"timeout did not fire promptly: {elapsed:.2f}s"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)
