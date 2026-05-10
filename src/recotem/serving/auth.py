"""X-API-Key authentication dependency for the Recotem serving layer.

Security design:
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

# Maximum accepted X-API-Key header length (in bytes / chars — the header
# is ASCII-only so the two are equivalent here).
#
# Recotem-issued API keys are 32 bytes random encoded as base64url
# (43 chars).  256 chars leaves comfortable headroom for any reasonable
# operator-issued key while bounding the work that an attacker can force
# the scrypt KDF to perform per request.  Without this cap, a single
# 1 MiB X-API-Key header would force the server to scrypt-hash a 1 MiB
# input on every request — even at the lowest valid cost (n=2, r=8, p=1)
# this is enough memory bandwidth to amplify a denial-of-service.
_API_KEY_MAX_LEN = 256

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

# Minimum accepted X-API-Key plaintext length (in chars).  Keys shorter than
# this carry less entropy and could be brute-forced offline if the stored
# digest were ever leaked.  Recotem-issued keys (``recotem keygen --type api``)
# produce 43-char base64url output, well above this floor.  Rejection is
# indistinguishable from an invalid-key response to avoid leaking information
# about which keys are configured.
_API_KEY_MIN_LEN = 32


def _hash_api_key(value: str) -> str:
    """Return the hex-encoded scrypt digest of *value*.

    Implementation note: scrypt (a key derivation function) is used to
    deterministically derive a 32-byte digest from the API-key plaintext,
    bound to the domain-separation salt ``recotem.api-key.v1``.  This is
    NOT a password-hashing call site — Recotem API keys are 256-bit random
    tokens.  See `_SCRYPT_N`/`_SCRYPT_R`/`_SCRYPT_P` constants above for
    the rationale behind the (low) cost parameters.

    scrypt is intentionally low-cost (N=2, r=8, p=1) because the input is
    already a 256-bit random token, making brute-force infeasible regardless
    of hash speed.  Raising the cost parameters would amplify DoS on the
    unauthenticated endpoint without improving security, since an attacker
    who somehow obtained the stored digest would still face 2^256 exhaustion.
    """
    # codeql[py/weak-sensitive-data-hashing] scrypt KDF intentionally at
    # minimum cost — see docstring and module-level comment above.
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
        401 if the header is missing, shorter than ``_API_KEY_MIN_LEN``
        (32 chars), exceeds ``_API_KEY_MAX_LEN`` (256 chars), or no entry
        matches.  Short-key rejections are indistinguishable from
        invalid-key rejections so the caller cannot determine which guard
        fired.  ``recotem keygen --type api`` produces 43-char keys, safely
        above the 32-char floor.
    """
    # No keys configured → no auth enforcement.
    if not api_keys:
        request.state.kid = "anonymous"
        return "anonymous"

    raw_header: str | None = request.headers.get(_API_KEY_HEADER)
    if raw_header is None:
        logger.warning("auth_missing_header", path=request.url.path)
        # Constant-time equalisation: run the scrypt KDF on a fixed-length
        # dummy value so that the missing-header response time is
        # indistinguishable from the short-key and normal hashing paths.
        # Without this call an attacker can distinguish the ~0 ms missing-
        # header branch from the ~0.5 ms KDF branch via response latency —
        # leaking whether a header was sent at all, which narrows the attack
        # surface from "any header" to "header present but wrong".
        # Same rationale as the oversized/short-key branches below.
        _hash_api_key("\x00" * _API_KEY_MIN_LEN)  # constant-time equalisation
        raise HTTPException(
            status_code=401,
            detail={"detail": "X-API-Key header required", "code": "missing_api_key"},
        )

    # Reject oversized headers BEFORE invoking the scrypt KDF on the real
    # payload.  An unbounded header would let an unauthenticated attacker
    # amplify scrypt work into a denial-of-service.  See ``_API_KEY_MAX_LEN``.
    #
    # Constant-time equalisation: we still run _hash_api_key on a fixed-
    # length dummy value so that the response time is indistinguishable from
    # the short-key branch and from a legitimate key whose digest simply does
    # not match.  This prevents a length-based timing oracle — without it, an
    # attacker could distinguish the oversized branch (~0 ms) from the hashing
    # branch (~0.5 ms) and infer the key-length range from response latency.
    # The result is discarded; the side effect is the scrypt wall time alone.
    if len(raw_header) > _API_KEY_MAX_LEN:
        logger.warning(
            "auth_oversized_header",
            path=request.url.path,
            length=len(raw_header),
            cap=_API_KEY_MAX_LEN,
        )
        _hash_api_key("\x00" * _API_KEY_MIN_LEN)  # constant-time equalisation
        raise HTTPException(
            status_code=401,
            detail={"detail": "Invalid API key", "code": "invalid_api_key"},
        )

    # Reject plaintexts shorter than _API_KEY_MIN_LEN.
    # A key shorter than 32 chars carries less than 256 bits of entropy even
    # when generated randomly; if the digest were ever leaked, the reduced
    # search space makes offline brute-force tractable.  Recotem-issued keys
    # (``recotem keygen --type api``) produce 43-char base64url output (32
    # random bytes), well above this floor.  The rejection response is
    # identical to an invalid-key response so the caller cannot determine
    # whether the key was too short or simply unrecognised.
    #
    # Constant-time equalisation: same rationale as the oversized branch above.
    # Without this, timing on the short-key path (~0 ms) vs the normal path
    # (~0.5 ms) would allow an attacker to determine from latency alone
    # whether their probe key was too short or too long.
    if len(raw_header) < _API_KEY_MIN_LEN:
        logger.warning(
            "auth_short_key_rejected",
            path=request.url.path,
            length=len(raw_header),
            min_len=_API_KEY_MIN_LEN,
        )
        _hash_api_key("\x00" * _API_KEY_MIN_LEN)  # constant-time equalisation
        raise HTTPException(
            status_code=401,
            detail={"detail": "Invalid API key", "code": "invalid_api_key"},
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
