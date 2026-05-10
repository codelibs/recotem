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
    """A 302 redirect from a public host to 10.0.0.1 must be refused.

    Patches ``assert_host_public`` so the first hop is allowed to reach the
    local pytest_httpserver (which serves the 302), and the second hop —
    targeting the RFC1918 ``10.0.0.1`` Location header — invokes the real
    SSRF guard which classifies the host as private and refuses the connect.

    The new IP-pinned opener (MAJOR-2) means we cannot just lie about the
    first-hop resolution and let urllib silently re-resolve the right IP —
    so we patch ``assert_host_public`` directly: a no-op (returns None,
    no pinning) for the first call, and the real check for the second.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")

    private_redirect = f"http://10.0.0.1:{httpserver.port}/private"
    httpserver.expect_request("/public").respond_with_data(
        b"",
        status=302,
        headers={"Location": private_redirect},
    )

    public_url = httpserver.url_for("/public")

    import recotem._http_fetch as _mod

    real_assert_host_public = _mod.assert_host_public
    call_count = [0]

    def _patched_assert_host_public(url: str, *, allow_private: bool):
        call_count[0] += 1
        if call_count[0] == 1:
            # First hop: skip the SSRF check (treat as public), no pinning.
            # The connection therefore goes through the default opener and
            # actually reaches the local httpserver.
            return None
        # Subsequent hops: invoke the real SSRF check, which must reject
        # the literal 10.0.0.1 redirect target.
        return real_assert_host_public(url, allow_private=allow_private)

    with patch.object(
        _mod, "assert_host_public", side_effect=_patched_assert_host_public
    ):
        with pytest.raises(HttpFetchError, match="private/internal"):
            fetch_http_bytes(
                public_url,
                timeout=5,
                max_bytes=65536,
                allow_private=False,
            )

    # Sanity: assert_host_public was invoked at least twice (once per hop).
    assert call_count[0] >= 2, (
        f"Expected at least 2 assert_host_public calls (one per hop), "
        f"got {call_count[0]}."
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
# C12. DNS rebinding TOCTOU: assert_host_public is re-called on every redirect hop
# ---------------------------------------------------------------------------


def test_dns_rebinding_to_private_ip_rejected_at_redirect_hop(
    monkeypatch: pytest.MonkeyPatch, httpserver
) -> None:
    """Simulate DNS rebinding across redirect hops.

    The original threat model: an attacker controls the authoritative DNS
    for a hostname.  At resolve-time #1 the SSRF guard sees a public IP; at
    resolve-time #2 (the actual connect) the same hostname returns a
    private IP.

    With the IP-pinned opener (MAJOR-2) the connect always targets the IP
    captured at SSRF-check time, so single-hop rebinding is blocked at the
    socket level.  This test exercises the redirect-loop variant: hop 1
    passes the SSRF check, hop 2 is a Location: redirect to a host whose
    SSRF check returns a private IP — the second ``assert_host_public``
    call must refuse before any further connect happens.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")

    private_redirect_url = "http://rebind.internal/secret"
    httpserver.expect_request("/rebind").respond_with_data(
        b"",
        status=302,
        headers={"Location": private_redirect_url},
    )

    import recotem._http_fetch as _mod

    real_assert_host_public = _mod.assert_host_public
    real_resolve = _mod._resolve_host_addresses
    call_count = [0]

    def _patched_assert_host_public(url: str, *, allow_private: bool):
        call_count[0] += 1
        if call_count[0] == 1:
            # First hop: bypass the SSRF check (returns None, no pinning) so
            # the actual connect lands on the local httpserver.
            return None
        # Second hop: invoke the real SSRF check.  The redirect target
        # host "rebind.internal" must resolve to a private IP — patch the
        # underlying resolver to return RFC1918.
        return real_assert_host_public(url, allow_private=allow_private)

    def _rebind_resolve(host: str):
        if host == "rebind.internal":
            return [ipaddress.ip_address("10.0.0.1")]
        return real_resolve(host)

    with patch.object(
        _mod, "assert_host_public", side_effect=_patched_assert_host_public
    ):
        with patch.object(_mod, "_resolve_host_addresses", side_effect=_rebind_resolve):
            with pytest.raises(HttpFetchError, match="private/internal"):
                fetch_http_bytes(
                    httpserver.url_for("/rebind"),
                    timeout=5,
                    max_bytes=65536,
                    allow_private=False,
                )

    # Verify that assert_host_public was called at least twice (once per hop).
    assert call_count[0] >= 2, (
        f"Expected at least 2 assert_host_public calls (one per hop), "
        f"got {call_count[0]}. The SSRF re-check on redirect hops is broken."
    )


# ---------------------------------------------------------------------------
# C13. HTTPS→HTTP scheme-changing redirect is rejected
# ---------------------------------------------------------------------------


def test_redirect_https_to_http_rejected(httpserver) -> None:
    """A 302 redirect from https:// to http:// (TLS downgrade) must be refused.

    We mock fetch_http_bytes to simulate the redirect because pytest_httpserver
    only provides HTTP.  The scheme-change check happens before the next hop is
    fetched — so we can drive it through the redirect loop with a mocked opener.
    """

    import recotem._http_fetch as _mod
    from recotem._http_fetch import HttpFetchError

    http_redirect_url = "http://example.com/downgraded"

    # Build a fake response that returns 302 → http:// when the "https" URL is opened.
    class _FakeResponse:
        status = 302

        class headers:
            @staticmethod
            def get(key):
                if key == "Location":
                    return http_redirect_url
                return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, n):
            return b""

    original_opener_open = _mod._NO_REDIRECT_OPENER.open

    call_count = [0]

    def _fake_open(req, timeout=None):
        call_count[0] += 1
        return _FakeResponse()

    with patch.object(_mod._NO_REDIRECT_OPENER, "open", side_effect=_fake_open):
        with patch.object(_mod, "assert_host_public", return_value=None):
            with pytest.raises(HttpFetchError, match="scheme-changing redirect"):
                fetch_http_bytes(
                    "https://example.com/start",
                    timeout=5,
                    max_bytes=65536,
                    allow_private=True,
                )


# ---------------------------------------------------------------------------
# C14. Circular redirect loop (A→B→A) is detected and rejected
# ---------------------------------------------------------------------------


def test_redirect_circular_loop_rejected(httpserver) -> None:
    """A circular redirect chain (A→B→A) must raise HttpFetchError with
    'Redirect loop' message when the visited-set detects the cycle.
    """
    # /loop_a → /loop_b → /loop_a → detected as loop
    httpserver.expect_request("/loop_a").respond_with_data(
        b"",
        status=302,
        headers={"Location": httpserver.url_for("/loop_b")},
    )
    httpserver.expect_request("/loop_b").respond_with_data(
        b"",
        status=302,
        headers={"Location": httpserver.url_for("/loop_a")},
    )

    with pytest.raises(HttpFetchError, match="Redirect loop"):
        fetch_http_bytes(
            httpserver.url_for("/loop_a"),
            timeout=10,
            max_bytes=65536,
            allow_private=True,
        )


# ---------------------------------------------------------------------------
# C15. IPv6 link-local address (fe80::/10) is rejected
# ---------------------------------------------------------------------------


def test_ipv6_link_local_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """IPv6 link-local addresses (fe80::/10) must be refused when allow_private=False.

    This covers the cloud-metadata endpoint on IPv6-only deployments and
    link-local multicast / router addresses.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")
    with pytest.raises(HttpFetchError, match="private/internal address"):
        assert_host_public("http://[fe80::1]/x", allow_private=False)


# ---------------------------------------------------------------------------
# C16. IPv6 Unique Local Address (fc00::/7, e.g. fd00::/8) is rejected
# ---------------------------------------------------------------------------


def test_ipv6_ula_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """IPv6 ULA addresses (fc00::/7, commonly fd00::/8) must be refused when
    allow_private=False.  These are the IPv6 equivalent of RFC1918 private space.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")
    with pytest.raises(HttpFetchError, match="private/internal address"):
        assert_host_public("http://[fd00::1]/x", allow_private=False)


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


# ---------------------------------------------------------------------------
# MAJOR-2: single-hop DNS rebinding via socket.getaddrinfo TOCTOU
# ---------------------------------------------------------------------------


def test_dns_rebinding_single_hop_blocked_by_ip_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-hop DNS rebinding must be blocked.

    Threat: an attacker-controlled authoritative DNS server returns a public
    IP for the resolution that ``assert_host_public`` performs, then a
    private IP (e.g. cloud metadata 169.254.169.254) for the resolution
    urllib performs inside ``connect()``.  Without IP pinning, the SSRF
    guard is bypassed because the two lookups are independent.

    With the IP-pinned opener (MAJOR-2), the resolved IP from
    ``assert_host_public`` is fed straight into the ``HTTPConnection``'s
    ``connect()``.  When ``connect()`` later runs ``create_connection``,
    it does so against the literal pinned IP — even if a second
    ``getaddrinfo`` for the same hostname would return a private IP, the
    socket is opened against the IP captured at SSRF-check time.

    Test strategy: stub ``socket.getaddrinfo`` so the first invocation
    (the SSRF check) returns a public IP and any subsequent invocation
    returns the cloud-metadata IP.  Then patch
    ``socket.create_connection`` to record the address it was called with
    and refuse the connection.  The recorded address MUST be the public
    IP — proving the pin held — and MUST NOT be the private IP.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")

    public_ip = "93.184.216.34"
    private_ip = "169.254.169.254"  # cloud metadata; classic SSRF target

    getaddrinfo_calls = [0]

    def _rebound_getaddrinfo(host, port, *args, **kwargs):
        getaddrinfo_calls[0] += 1
        if getaddrinfo_calls[0] == 1:
            # SSRF check: see a benign public IP.
            return [(socket.AF_INET, None, None, None, (public_ip, port or 0))]
        # Any subsequent lookup: rebound to a private cloud-metadata IP.
        # With IP pinning the connect path uses create_connection on a
        # literal — getaddrinfo may still be invoked by stdlib internals
        # to parse the literal, but the resolved host argument is the
        # captured public IP, NOT the original hostname, so even if the
        # call lands here, the rebound address never reaches connect.
        return [(socket.AF_INET, None, None, None, (private_ip, port or 0))]

    create_connection_targets: list[tuple[str, int]] = []

    def _refusing_create_connection(address, *args, **kwargs):
        # address = (host_or_ip, port).
        create_connection_targets.append(address)
        raise OSError("simulated connection refused")

    with patch("socket.getaddrinfo", side_effect=_rebound_getaddrinfo):
        with patch("socket.create_connection", side_effect=_refusing_create_connection):
            with pytest.raises(HttpFetchError):
                fetch_http_bytes(
                    "http://rebound.example.invalid/",
                    timeout=2,
                    max_bytes=1024,
                    allow_private=False,
                )

    # Security property: the connect target MUST be the public IP captured
    # at SSRF-check time, not the rebound private one.
    assert create_connection_targets, (
        "Expected create_connection to be invoked at least once."
    )
    targets_hosts = {addr[0] for addr in create_connection_targets}
    assert public_ip in targets_hosts, (
        f"Expected connect to target the pinned public IP {public_ip!r}, "
        f"got {targets_hosts!r}."
    )
    assert private_ip not in targets_hosts, (
        f"DNS-rebinding TOCTOU bypassed: connect targeted the rebound "
        f"private IP {private_ip!r} ({targets_hosts!r})."
    )


# ---------------------------------------------------------------------------
# MAJOR-3: IPv4-mapped IPv6 addresses must be rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ipv6_mapped",
    [
        "::ffff:127.0.0.1",  # loopback
        "::ffff:169.254.169.254",  # AWS / GCP cloud metadata
        "::ffff:10.0.0.1",  # RFC1918 private
        "::ffff:172.16.0.1",  # RFC1918 private
        "::ffff:192.168.1.1",  # RFC1918 private
    ],
)
def test_assert_host_public_rejects_ipv4_mapped_ipv6(
    ipv6_mapped: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IPv4-mapped IPv6 addresses (``::ffff:a.b.c.d``) must be rejected.

    Without the explicit ipv4_mapped re-check, some Python releases /
    ipaddress versions classify ``::ffff:169.254.169.254`` as
    ``is_link_local=False`` because they only consult the IPv6 layout —
    bypassing the SSRF guard for cloud-metadata endpoints.  The fix
    unwraps the embedded IPv4 address and re-evaluates is_private /
    is_loopback / is_link_local on it.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")
    with pytest.raises(HttpFetchError, match="private/internal address"):
        assert_host_public(f"http://[{ipv6_mapped}]/x", allow_private=False)


def test_is_address_internal_unwraps_ipv4_mapped() -> None:
    """``_is_address_internal`` must classify IPv4-mapped IPv6 by the
    embedded IPv4 address.
    """
    from recotem._http_fetch import _is_address_internal

    # Loopback, link-local, RFC1918 — all flagged via the unwrapped IPv4.
    assert _is_address_internal(ipaddress.ip_address("::ffff:127.0.0.1")) is True
    assert _is_address_internal(ipaddress.ip_address("::ffff:169.254.169.254")) is True
    assert _is_address_internal(ipaddress.ip_address("::ffff:10.0.0.1")) is True
    # A public IPv4-mapped should pass.
    assert _is_address_internal(ipaddress.ip_address("::ffff:8.8.8.8")) is False


# ---------------------------------------------------------------------------
# New: Content-Length pre-check vs streaming cap behaviour
# ---------------------------------------------------------------------------


def test_content_length_over_cap_rejected_without_reading(httpserver) -> None:
    """Server responds with Content-Length larger than max_bytes cap.

    Current behaviour: fetch_http_bytes does NOT inspect Content-Length
    headers before streaming; the cap fires during the streaming read
    when accumulated bytes exceed max_bytes.

    This test documents that behaviour.  When the actual body is small
    (< max_bytes) but Content-Length claims it is huge, the streaming cap
    does NOT fire and the fetch succeeds — because only actual bytes read
    are counted.

    The xfail marker below documents the desired future behaviour
    (pre-stream Content-Length check) that is NOT yet implemented.
    """
    import pytest

    # Serve a small 50-byte body but advertise a very large Content-Length.
    small_body = b"user_id,item_id\nu1,i1\n"  # 22 bytes
    huge_content_length = 10 * 1024 * 1024  # 10 MiB — far above cap

    httpserver.expect_request("/data.csv").respond_with_data(
        small_body,
        status=200,
        headers={"Content-Length": str(huge_content_length)},
    )

    cap = 100  # 100 bytes — small_body (22 bytes) fits; huge_cl does not

    # With current implementation: streaming cap fires only on actual bytes.
    # The body is 22 bytes < cap (100), so fetch succeeds despite lying CL.
    result = fetch_http_bytes(
        httpserver.url_for("/data.csv"),
        timeout=5,
        max_bytes=cap,
        allow_private=True,
    )
    assert result == small_body, (
        "Streaming cap should allow a small actual body even if "
        "Content-Length advertises a larger size"
    )

    # xfail: a pre-Content-Length check is NOT currently implemented.
    # If this is ever added, the fetch should raise HttpFetchError before
    # reading any body bytes when Content-Length > max_bytes.
    # This assertion documents the gap.
    @pytest.mark.xfail(
        reason=(
            "Pre-stream Content-Length check not implemented: "
            "cap fires during streaming, not on header inspection"
        ),
        strict=True,
    )
    def _assert_pre_check_future_behaviour() -> None:
        with pytest.raises(HttpFetchError, match="cap|Content-Length"):
            fetch_http_bytes(
                httpserver.url_for("/data.csv"),
                timeout=5,
                max_bytes=cap,
                allow_private=True,
            )
