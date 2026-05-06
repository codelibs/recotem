"""X-API-Key authentication dependency for the Recotem serving layer.

Security design (spec Section 9):
- Keys are 32 bytes, base64url-encoded (43 chars) at the plaintext level.
- Server stores ``sha256(plaintext)`` as a 64-char hex string.
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


def _sha256_hex(value: str) -> str:
    """Return the hex-encoded SHA-256 digest of a UTF-8 string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    candidate_hash = _sha256_hex(raw_header)

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
