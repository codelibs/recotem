"""X-API-Key authentication dependency for the Recotem serving layer.

Security design (spec Section 9):
- Keys are 32 bytes, base64url-encoded (43 chars) at the plaintext level.
- Server stores ``HMAC-SHA256(label, plaintext)`` as a 64-char hex string,
  where ``label = b"recotem.api-key.v1"`` provides domain separation so the
  stored value cannot be substituted into another context that also stores
  raw SHA-256 digests.  The wire format on disk / in env remains
  ``<kid>:sha256:<hex64>`` for backward compatibility — the ``sha256`` token
  identifies the digest family, not the keyless construction.
- Constant-time compare via ``hmac.compare_digest`` prevents timing attacks.
- Whitespace in the incoming ``X-API-Key`` header is **not** stripped — any
  leading/trailing whitespace is treated as part of the key and will produce
  a deterministic 401.
- The matching ``kid`` (never the plaintext key or hash) is attached to
  ``request.state.kid`` for use in structured logging.
- Auth failures are logged at WARNING level with no key material in the event.
"""

from __future__ import annotations

import hashlib
import hmac

import structlog
from fastapi import HTTPException, Request

from recotem.config import ApiKeyEntry

logger = structlog.get_logger(__name__)

# Header name used by the client (lowercase for case-insensitive lookup).
_API_KEY_HEADER = "x-api-key"

# Domain-separation label for API key hashing.  Keyed HMAC ensures the stored
# digest is bound to this specific use (API key verification) and cannot be
# substituted into any other context that hashes the same plaintext.  Treated
# as a fixed protocol constant — bumping the ``v1`` suffix would invalidate
# all stored hashes and require re-issuing keys.
_API_KEY_HMAC_LABEL = b"recotem.api-key.v1"


def _hash_api_key(value: str) -> str:
    """Return the hex-encoded HMAC-SHA256 of *value* under the API-key label.

    This is **not** a password hash — API keys are 256-bit random tokens, so
    a single HMAC iteration is sufficient (matches the GitHub / Stripe model).
    The keyed construction provides domain separation versus a bare SHA-256
    digest, preventing cross-context hash reuse.
    """
    return hmac.new(
        _API_KEY_HMAC_LABEL, value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_api_key(request: Request, api_keys: list[ApiKeyEntry]) -> str:
    """Verify the ``X-API-Key`` header and return the matching ``kid``.

    Parameters
    ----------
    request:
        The current FastAPI request.  The matching kid is stored on
        ``request.state.kid`` on success.
    api_keys:
        The list of configured :class:`~recotem.config.ApiKeyEntry` objects.
        If the list is empty, all requests are allowed and ``kid`` is set to
        ``"anonymous"``.

    Returns
    -------
    str
        The matching ``kid``.

    Raises
    ------
    fastapi.HTTPException
        401 if the header is missing or no entry matches.
    """
    # No keys configured → no auth enforcement.
    if not api_keys:
        request.state.kid = "anonymous"
        return "anonymous"

    raw_header: str | None = request.headers.get(_API_KEY_HEADER)
    if raw_header is None:
        logger.warning("auth_missing_header", path=request.url.path)
        raise HTTPException(
            status_code=401,
            detail={"detail": "X-API-Key header required", "code": "missing_api_key"},
        )

    # No stripping — whitespace is part of the key.
    candidate_hash = _hash_api_key(raw_header)

    for entry in api_keys:
        # hmac.compare_digest operates on equal-length strings; both are 64
        # lowercase hex chars so the lengths always match.
        if hmac.compare_digest(candidate_hash, entry.sha256_hex):
            request.state.kid = entry.kid
            return entry.kid

    logger.warning("auth_invalid_key", path=request.url.path)
    raise HTTPException(
        status_code=401,
        detail={"detail": "Invalid API key", "code": "invalid_api_key"},
    )
