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
import urllib.error
import urllib.request
from urllib.parse import urlparse, urlunparse

import structlog

logger = structlog.get_logger(__name__)


class HttpFetchError(Exception):
    """Raised by HTTP-fetch helpers on any I/O, redirect, sha256, or cap issue."""


_USER_AGENT = "recotem/2"
_CHUNK_SIZE = 1 * 1024 * 1024  # 1 MiB
MAX_REDIRECTS = 5

NETWORK_SCHEMES: frozenset[str] = frozenset({"http", "https"})

_USERINFO_SCHEMES: frozenset[str] = frozenset({"http", "https", "ftp", "ftps"})

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


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoFollowRedirectHandler())


def fetch_http_bytes(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    log_event: str = "http_fetch",
    log_context: dict[str, object] | None = None,
) -> bytes:
    """GET *url*, returning the body bytes.

    Streams into memory with a hard ``max_bytes`` cap, follows up to
    :data:`MAX_REDIRECTS` 3xx hops (only http(s) targets), and raises
    :class:`HttpFetchError` for any HTTP / network / cap-exceeded error.

    *log_event* is the structlog event name used for redirect / cap-exceeded
    log lines; *log_context* is merged into those log records (caller-provided
    correlation IDs such as recipe name, run id).
    """
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

        req = urllib.request.Request(
            current_url,
            headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
            method="GET",
        )
        try:
            with _NO_REDIRECT_OPENER.open(req, timeout=timeout) as resp:
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
