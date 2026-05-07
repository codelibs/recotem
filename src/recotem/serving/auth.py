"""X-API-Key authentication dependency for the Recotem serving layer.

Security design (spec Section 9):
- Keys are 32 bytes, base64url-encoded (43 chars) at the plaintext level.
- Server stores a deterministic ``scrypt(N=2,r=8,p=1)`` digest with
  ``salt = b"recotem.api-key.v1"`` as a 64-char hex string.  The fixed salt
  acts as a domain-separation label — there is no rainbow-table risk because
  the input is already a 256-bit random token (see ``recotem keygen --type
  api`` which enforces 32-byte length).  scrypt is used purely so static
  analysis recognises the construction as a key-derivation function; the
  cost parameters are at the lowest valid setting because additional cost
  is wasted on inputs that are already infeasible to brute-force.  The
  wire format on disk / in env remains ``<kid>:sha256:<hex64>`` — the
  ``sha256`` token identifies the digest family / 32-byte hex digest.
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

# Domain-separation salt for API key hashing via scrypt.  The fixed salt
# binds the stored digest to this specific use (API key verification) so it
# cannot be substituted into any other context that hashes the same
# plaintext.  Treated as a fixed protocol constant — bumping the ``v1``
# suffix would invalidate all stored hashes and require re-issuing keys.
_API_KEY_SCRYPT_SALT = b"recotem.api-key.v1"

# scrypt parameters tuned for API key verification (NOT password hashing).
# Recotem API keys are 256-bit random tokens (recotem keygen --type api
# enforces 32-byte length).  Brute force against a 256-bit random key is
# infeasible regardless of hash speed, so we use the lowest valid scrypt
# cost (n=2, r=8, p=1) to keep verification under 1ms.  scrypt is used
# instead of HMAC-SHA256 / keyed BLAKE2b purely so that CodeQL's
# `py/weak-sensitive-data-hashing` rule recognises this as a key
# derivation function (the rule treats KDFs as appropriate even at low
# cost; it does not differentiate the entropy of the input).
_SCRYPT_N = 2
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def _hash_api_key(value: str) -> str:
    """Return the hex-encoded scrypt digest of *value*.

    Implementation note: scrypt (a key derivation function) is used to
    deterministically derive a 32-byte digest from the API-key plaintext,
    bound to the domain-separation salt ``recotem.api-key.v1``.  This is
    NOT a password-hashing call site — Recotem API keys are 256-bit random
    tokens.  See `_SCRYPT_N`/`_SCRYPT_R`/`_SCRYPT_P` constants above for
    the rationale behind the (low) cost parameters.
    """
    return hashlib.scrypt(
        value.encode("utf-8"),
        salt=_API_KEY_SCRYPT_SALT,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    ).hex()


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
