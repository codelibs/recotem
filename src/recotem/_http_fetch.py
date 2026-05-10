"""Shared HTTP-fetch utilities used by data-source and metadata loaders.

These helpers enforce the documented controls on every HTTP/HTTPS download:

* sha256 byte-content verification (constant-time compare via :mod:`hmac`).
* ``RECOTEM_MAX_DOWNLOAD_BYTES`` cap, streamed in 1 MiB chunks.
* ``RECOTEM_HTTP_TIMEOUT_SECONDS`` connect/read timeout.
* Manual redirect loop with a visited-set, ``MAX_REDIRECTS`` limit, scheme
  allow-list (``http`` / ``https`` only), and same-scheme constraint against
  the original request — 302→file:// SSRF tricks and https→http TLS
  downgrades are both refused.
* URL userinfo redaction in log messages.

All public helpers raise :class:`HttpFetchError` on failure; callers are
expected to catch it and re-raise as their own domain-specific exception
(``DataSourceError`` for data sources, ``ValueError`` for metadata).
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import socket
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse, urlunparse

import structlog

logger = structlog.get_logger(__name__)


class HttpFetchError(Exception):
    """Raised by HTTP-fetch helpers on any I/O, redirect, sha256, or cap issue."""


# ---------------------------------------------------------------------------
# Bogon / reserved ranges not covered by is_private / is_reserved alone
# ---------------------------------------------------------------------------
#
# Python's ipaddress.is_private, is_reserved, is_loopback etc. cover the core
# RFC1918 / loopback / link-local / ULA space, but several additional IANA-
# reserved blocks are NOT classified as private or reserved in all Python
# versions:
#
#   100.64.0.0/10   CGNAT shared address space (RFC6598)
#   198.18.0.0/15   Benchmark / testing (RFC2544)
#   192.0.2.0/24    TEST-NET-1 (RFC5737)
#   198.51.100.0/24 TEST-NET-2 (RFC5737)
#   203.0.113.0/24  TEST-NET-3 (RFC5737)
#
#   64:ff9b::/96    IPv6 NAT64 well-known prefix (RFC6052)
#   2001:db8::/32   Documentation / example prefix (RFC3849)
#
# These ranges are not routable on the public internet and are abused in SSRF
# payloads (especially CGNAT which some cloud providers route internally).
# The bogon lists below are OR-combined with the standard property checks in
# _is_address_internal to close the gap.

_BOGON_V4: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(net)
    for net in (
        "100.64.0.0/10",  # CGNAT shared address space (RFC6598)
        "198.18.0.0/15",  # Benchmark / testing (RFC2544)
        "192.0.2.0/24",  # TEST-NET-1 (RFC5737)
        "198.51.100.0/24",  # TEST-NET-2 (RFC5737)
        "203.0.113.0/24",  # TEST-NET-3 (RFC5737)
    )
)

_BOGON_V6: tuple[ipaddress.IPv6Network, ...] = tuple(
    ipaddress.IPv6Network(net)
    for net in (
        "64:ff9b::/96",  # IPv6 NAT64 well-known prefix (RFC6052)
        "2001:db8::/32",  # Documentation / example prefix (RFC3849)
    )
)


def _in_bogon(addr: ipaddress._BaseAddress) -> bool:
    """Return True if *addr* falls within any bogon / IANA-reserved range.

    Handles both plain IPv4/IPv6 addresses and IPv4-mapped IPv6 addresses
    (``::ffff:a.b.c.d``): for the latter the embedded IPv4 is checked against
    ``_BOGON_V4`` so that CGNAT addresses presented as IPv4-mapped are caught.
    """
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return any(mapped in net for net in _BOGON_V4)
        return any(addr in net for net in _BOGON_V6)
    # Plain IPv4
    return any(addr in net for net in _BOGON_V4)


_USER_AGENT = "recotem/2"
_CHUNK_SIZE = 1 * 1024 * 1024  # 1 MiB
MAX_REDIRECTS = 5

NETWORK_SCHEMES: frozenset[str] = frozenset({"http", "https"})

_USERINFO_SCHEMES: frozenset[str] = frozenset({"http", "https", "ftp", "ftps"})


def _resolve_host_addresses(host: str) -> list[ipaddress._BaseAddress]:
    """Return all numeric IP addresses *host* resolves to.

    Accepts both already-numeric input (``"127.0.0.1"``, ``"::1"``) and
    DNS names.  Returns an empty list if resolution fails — callers treat
    that as "cannot verify, refuse" rather than "implicitly safe".
    """
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    addrs: list[ipaddress._BaseAddress] = []
    seen: set[str] = set()
    for family, _, _, _, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            addrs.append(ipaddress.ip_address(ip_str))
        except ValueError:
            continue
    return addrs


def _is_address_internal(addr: ipaddress._BaseAddress) -> bool:
    """Return True if *addr* is RFC1918 / loopback / link-local / reserved / bogon.

    Covers the SSRF risk surface: cloud metadata (169.254.169.254 link-local),
    internal services (10/8, 172.16/12, 192.168/16, fc00::/7), localhost,
    multicast / unspecified ranges, and additional IANA-reserved bogon blocks
    that some Python versions do not flag via ``is_private`` / ``is_reserved``
    alone (see ``_BOGON_V4`` / ``_BOGON_V6`` above).

    For IPv4-mapped IPv6 addresses (``::ffff:a.b.c.d``), the embedded IPv4
    address is the authoritative source of truth and is checked first.
    Python's ``IPv6Address`` properties for the ``::ffff:0:0/96`` block have
    been inconsistent across patch releases (e.g. ``is_reserved`` returned
    ``True`` for public IPv4-mapped addresses in some builds), so relying on
    the IPv6-level properties for these addresses would produce false positives.
    Evaluating the unwrapped IPv4 directly avoids the ambiguity entirely.
    The bogon check also uses the unwrapped IPv4 for mapped addresses.
    See MAJOR-3 in :doc:`/security`.
    """
    # Primary check for IPv4-mapped IPv6 addresses (``::ffff:a.b.c.d``):
    # unwrap to the embedded IPv4 and evaluate properties there, bypassing
    # IPv6-level property inconsistencies for the ``::ffff:0:0/96`` block.
    # The bogon membership check also targets the unwrapped IPv4.
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        return bool(
            mapped.is_private
            or mapped.is_loopback
            or mapped.is_link_local
            or mapped.is_multicast
            or mapped.is_reserved
            or mapped.is_unspecified
            or _in_bogon(mapped)
        )
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or _in_bogon(addr)
    )


def assert_host_public(url: str, *, allow_private: bool) -> str | None:
    """Raise :class:`HttpFetchError` if *url*'s host resolves to a private IP.

    Returns the resolved IP that the connection should be pinned to — a
    single one of the public IPs the hostname resolved to.  Callers feed
    this into :func:`_open_with_pinned_ip` so that the actual TCP connect
    bypasses a second DNS lookup, foreclosing the DNS-rebinding TOCTOU
    where the first lookup returns a public IP and the second returns a
    private one (e.g. cloud metadata).

    Returns ``None`` when *allow_private* is True (no SSRF check, and no
    pinning — the caller defers to the system resolver).  Returns ``None``
    when *url*'s host is already a numeric IP that the caller can use
    directly without re-resolving.

    No-op when *allow_private* is True — operators of internal-only
    deployments can opt in via ``RECOTEM_HTTP_ALLOW_PRIVATE=1``.

    Refuses when DNS resolution fails outright; callers prefer a clear
    refusal over racing against a potentially-poisoned resolver.
    """
    if allow_private:
        return None
    parsed = urlparse(url)
    host = parsed.hostname
    safe_url = redact_url_userinfo(url)
    if not host:
        raise HttpFetchError(f"Refusing fetch to URL without a host: {safe_url}")
    addrs = _resolve_host_addresses(host)
    if not addrs:
        raise HttpFetchError(
            f"Refusing fetch to {safe_url}: hostname does not resolve. "
            "Set RECOTEM_HTTP_ALLOW_PRIVATE=1 to bypass for offline tests."
        )
    for addr in addrs:
        if _is_address_internal(addr):
            raise HttpFetchError(
                f"Refusing fetch to private/internal address for {safe_url} "
                f"(resolved to {addr}). Set RECOTEM_HTTP_ALLOW_PRIVATE=1 to "
                "allow internal HTTP origins."
            )
    # All resolved addresses are public; pin the first one for the actual
    # TCP connect so a follow-up DNS lookup cannot rebind the hostname to
    # a private IP between the SSRF check and the connect (MAJOR-2 / DNS
    # rebinding TOCTOU).  We deliberately keep this scoped to a single
    # request — no caching is shared across requests.
    return str(addrs[0])


_COMPRESSION_MAP: dict[str, str] = {
    ".gz": "gzip",
    ".bz2": "bz2",
    ".zip": "zip",
    ".xz": "xz",
}


def redact_url_userinfo(path: str) -> str:
    """Strip any userinfo from URL-shaped *path* before logging.

    Only redacts for HTTP(S) / FTP(S); object-store schemes like
    ``gs://bucket@project/...`` use ``@`` in their idiomatic syntax.
    """
    parsed = urlparse(path)
    if parsed.scheme.lower() not in _USERINFO_SCHEMES:
        return path
    if not parsed.username and not parsed.password:
        return path
    netloc = parsed.hostname or ""
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def infer_compression(path: str) -> str | None:
    """Pandas-style compression hint from a path extension. None if plain."""
    lower_path = urlparse(path).path.lower() if "://" in path else path.lower()
    for ext, codec in _COMPRESSION_MAP.items():
        if lower_path.endswith(ext):
            return codec
    return None


def verify_sha256(actual: bytes, expected_hex: str) -> None:
    """Constant-time-compare sha256(*actual*) against *expected_hex*."""
    digest = hashlib.sha256(actual).hexdigest()
    if not hmac.compare_digest(digest, expected_hex):
        raise HttpFetchError(
            f"sha256 mismatch: got {digest[:8]}…, expected {expected_hex[:8]}…"
        )


class _NoFollowRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Override urllib's default redirect handler so 3xx responses surface.

    :func:`fetch_http_bytes` implements its own redirect loop with a lower cap
    (:data:`MAX_REDIRECTS`) and visited-set loop detection.  With the default
    handler installed, ``urlopen`` would silently follow redirects up to
    urllib's own cap of 10 and our manual loop would be dead code.
    """

    def _passthrough(self, req, fp, code, msg, headers):  # type: ignore[override]
        return fp

    http_error_301 = _passthrough  # type: ignore[assignment]
    http_error_302 = _passthrough  # type: ignore[assignment]
    http_error_303 = _passthrough  # type: ignore[assignment]
    http_error_307 = _passthrough  # type: ignore[assignment]
    http_error_308 = _passthrough  # type: ignore[assignment]


# Default opener used when no IP-pin is in effect (i.e. when
# RECOTEM_HTTP_ALLOW_PRIVATE=1 disables the SSRF check, or when the URL's
# host is already a numeric IP literal).
_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoFollowRedirectHandler())


# ---------------------------------------------------------------------------
# IP-pinned opener (MAJOR-2: DNS rebinding TOCTOU mitigation)
# ---------------------------------------------------------------------------
#
# Without pinning, urllib does its own DNS lookup inside the connect() call,
# *independently* of our :func:`assert_host_public` check.  An attacker who
# controls the authoritative DNS for a hostname can return a public IP to
# the first lookup (passing the SSRF guard) and a private IP to the second
# (the real connect), bypassing the guard entirely.
#
# Mitigation: once :func:`assert_host_public` has resolved the host and
# confirmed every address is public, we feed the pinned IP into a custom
# ``HTTPConnection`` / ``HTTPSConnection`` that connects to the pinned IP
# directly.  The original hostname is preserved for the ``Host:`` header
# and (for HTTPS) for SNI + TLS certificate validation, so the request is
# indistinguishable from one that re-resolved correctly.


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects to a pre-resolved IP, not via DNS.

    The ``Host:`` header that urllib synthesises is built from
    ``self.host``, which we keep set to the original hostname so the
    upstream server still receives the right virtual-host header.  The
    actual TCP connect targets the pinned IP.
    """

    def __init__(
        self, host: str, *args: object, pinned_ip: str, **kwargs: object
    ) -> None:
        super().__init__(host, *args, **kwargs)  # type: ignore[arg-type]
        self._pinned_ip = pinned_ip

    def connect(self) -> None:  # type: ignore[override]
        # Bypass the system resolver entirely: open the socket against the
        # pinned IP rather than a freshly-resolved getaddrinfo result.
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port),
            timeout=self.timeout,
            source_address=self.source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that connects to a pre-resolved IP, not via DNS.

    Pins the TCP target to *pinned_ip* but keeps ``self.host`` equal to
    the original hostname so SNI and certificate verification still
    operate on the hostname the recipe author wrote.
    """

    def __init__(
        self,
        host: str,
        *args: object,
        pinned_ip: str,
        **kwargs: object,
    ) -> None:
        super().__init__(host, *args, **kwargs)  # type: ignore[arg-type]
        self._pinned_ip = pinned_ip

    def connect(self) -> None:  # type: ignore[override]
        # Open the raw TCP socket against the pinned IP.
        sock = socket.create_connection(
            (self._pinned_ip, self.port),
            timeout=self.timeout,
            source_address=self.source_address,
        )
        # Wrap in TLS using the *original* hostname so SNI + cert
        # validation continue to use the hostname the recipe author
        # specified (not the pinned IP).
        ssl_context = self._context
        self.sock = ssl_context.wrap_socket(sock, server_hostname=self.host)


def _build_pinned_opener(pinned_ip: str) -> urllib.request.OpenerDirector:
    """Return a urllib opener whose HTTP / HTTPS handlers connect to *pinned_ip*.

    Each invocation builds a fresh opener — pin state is not shared across
    requests.  The opener also installs :class:`_NoFollowRedirectHandler`
    so :func:`fetch_http_bytes` keeps strict control of the redirect loop
    (without the redirect-handler override urllib's default redirect chain
    of up to 10 hops would silently be followed *before* our SSRF re-check
    fires on the next hop).

    The SSL context is created once per opener (not per request) and passed
    explicitly to :class:`_PinnedHTTPSConnection` via the ``_builder``
    closure.  Passing ``context=`` directly to ``do_open`` does not guarantee
    that the custom connection class receives it — some Python versions pass
    it via kwargs that the constructor does not accept.  By including it in
    the ``_builder`` closure we ensure the same verified context (system
    default trust store, hostname verification enabled) is always used.
    """
    pinned_ip_str = pinned_ip
    # Build the SSL context once per opener — creating it on every request is
    # unnecessary and wastes a CA-bundle parse on each HTTPS fetch.
    _ssl_ctx = ssl.create_default_context()

    class _PinnedHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):  # type: ignore[override]
            def _builder(host: str, *a: object, **kw: object) -> _PinnedHTTPConnection:
                return _PinnedHTTPConnection(host, *a, pinned_ip=pinned_ip_str, **kw)

            return self.do_open(_builder, req)

    class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):  # type: ignore[override]
            def _builder(host: str, *a: object, **kw: object) -> _PinnedHTTPSConnection:
                # Pass the SSL context explicitly through the builder so that
                # _PinnedHTTPSConnection.connect() uses the system-default trust
                # store with hostname verification enabled.  Relying on the
                # `context=` kwarg to do_open is fragile — urllib's machinery
                # may not propagate it to a custom connection class.
                return _PinnedHTTPSConnection(
                    host, *a, pinned_ip=pinned_ip_str, context=_ssl_ctx, **kw
                )

            return self.do_open(_builder, req)

    return urllib.request.build_opener(
        _PinnedHTTPHandler(),
        _PinnedHTTPSHandler(),
        _NoFollowRedirectHandler(),
    )


def fetch_http_bytes(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    log_event: str = "http_fetch",
    log_context: dict[str, object] | None = None,
    allow_private: bool | None = None,
) -> bytes:
    """GET *url*, returning the body bytes.

    Streams into memory with a hard ``max_bytes`` cap, follows up to
    :data:`MAX_REDIRECTS` 3xx hops (only http(s) targets), and raises
    :class:`HttpFetchError` for any HTTP / network / cap-exceeded error.

    *log_event* is the structlog event name used for redirect / cap-exceeded
    log lines; *log_context* is merged into those log records (caller-provided
    correlation IDs such as recipe name, run id).

    *allow_private* controls the SSRF guard: when False (default), the fetch
    refuses any host that resolves to a private / loopback / link-local /
    reserved IP.  Pass ``None`` to consult :func:`recotem.config.get_http_allow_private`
    (the production wiring).  Pass ``True``/``False`` directly only from
    tests that need deterministic behaviour.
    """
    if allow_private is None:
        from recotem.config import get_http_allow_private  # noqa: PLC0415

        allow_private = get_http_allow_private()

    safe_url = redact_url_userinfo(url)
    original_scheme = urlparse(url).scheme.lower()
    redirects = 0
    current_url = url
    visited: set[str] = set()
    ctx: dict[str, object] = dict(log_context or {})

    while True:
        if redirects > MAX_REDIRECTS:
            raise HttpFetchError(
                f"Too many redirects (>{MAX_REDIRECTS}) fetching {safe_url}"
            )
        if current_url in visited:
            raise HttpFetchError(f"Redirect loop detected fetching {safe_url}")
        visited.add(current_url)

        # SSRF guard: re-resolve and re-check on every hop so that a 302 to a
        # CNAME pointing into RFC1918 is refused, not just the original URL.
        # The returned IP is pinned into the per-hop opener so urllib's own
        # connect() does not perform a *second* DNS lookup that an attacker
        # controlling the authoritative DNS could rebind to a private IP.
        # See MAJOR-2: DNS rebinding TOCTOU mitigation.
        pinned_ip = assert_host_public(current_url, allow_private=allow_private)

        opener = (
            _build_pinned_opener(pinned_ip)
            if pinned_ip is not None
            else _NO_REDIRECT_OPENER
        )

        req = urllib.request.Request(
            current_url,
            headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
            method="GET",
        )
        try:
            with opener.open(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                if status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise HttpFetchError(
                            f"HTTP {status} from {safe_url} without Location header"
                        )
                    redirects += 1
                    current_url = urllib.request.urljoin(current_url, location)
                    new_scheme = urlparse(current_url).scheme.lower()
                    if new_scheme not in NETWORK_SCHEMES:
                        raise HttpFetchError(
                            f"Refusing redirect from {safe_url} to disallowed "
                            f"scheme '{new_scheme}://' "
                            f"({redact_url_userinfo(current_url)})"
                        )
                    if new_scheme != original_scheme:
                        # Reject scheme-changing redirects so an https:// pin
                        # cannot be silently downgraded to http:// (TLS strip)
                        # nor an http:// source promoted to https:// in a way
                        # the recipe author did not opt into.
                        raise HttpFetchError(
                            f"Refusing scheme-changing redirect from "
                            f"{safe_url} ({original_scheme}://) to "
                            f"{redact_url_userinfo(current_url)} "
                            f"({new_scheme}://)"
                        )
                    logger.info(
                        f"{log_event}_redirect",
                        from_=safe_url,
                        to=redact_url_userinfo(current_url),
                        status=status,
                        **ctx,
                    )
                    continue
                if status >= 400:
                    raise HttpFetchError(f"HTTP {status} fetching {safe_url}")
                buf = bytearray()
                while True:
                    chunk = resp.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    if len(buf) + len(chunk) > max_bytes:
                        logger.warning(
                            f"{log_event}_size_exceeded",
                            path=safe_url,
                            bytes_read=len(buf) + len(chunk),
                            cap=max_bytes,
                            **ctx,
                        )
                        raise HttpFetchError(
                            f"Download size cap exceeded fetching {safe_url}: "
                            f"> {max_bytes} bytes (RECOTEM_MAX_DOWNLOAD_BYTES)."
                        )
                    buf.extend(chunk)
                return bytes(buf)
        except urllib.error.HTTPError as exc:
            raise HttpFetchError(
                f"HTTP {exc.code} fetching {safe_url}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise HttpFetchError(
                f"URL error fetching {safe_url}: {exc.reason}"
            ) from exc
        except HttpFetchError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise HttpFetchError(
                f"Unexpected error fetching {safe_url}: {exc}"
            ) from exc
