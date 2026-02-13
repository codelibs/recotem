"""Pure-Python HMAC-SHA256 signing and verification for pickle files.

This module contains no Django dependencies, allowing it to be used by
external services (e.g. the FastAPI inference service) that need to verify
pickle file integrity without a Django installation.

File format: HMAC_SIGNATURE (32 bytes) + PICKLE_PAYLOAD (remaining bytes)
"""

import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)

HMAC_SIZE = 32  # SHA-256 digest size


def compute_hmac(key: bytes, payload: bytes) -> bytes:
    """Compute HMAC-SHA256 of payload using the given key."""
    return hmac.new(key, payload, hashlib.sha256).digest()


def sign_pickle_bytes(key: bytes, payload: bytes) -> bytes:
    """Prepend HMAC-SHA256 signature to pickle payload."""
    signature = compute_hmac(key, payload)
    return signature + payload


def verify_and_extract(key: bytes, data: bytes, allow_legacy: bool = True) -> bytes:
    """Verify HMAC signature and return the pickle payload.

    For unsigned legacy files (pre-signing migration), the data is returned
    as-is and a warning is logged — but only when allow_legacy is True.

    Raises ValueError if the signature is present but invalid.
    """
    if len(data) <= HMAC_SIZE:
        if allow_legacy:
            logger.warning(
                "Loading unsigned pickle file (legacy). "
                "Run 'manage.py resign_models' and set "
                "PICKLE_ALLOW_LEGACY_UNSIGNED=false."
            )
            return data
        raise ValueError(
            "Pickle file too short and legacy unsigned files are not allowed. "
            "Run 'manage.py resign_models' to sign existing files."
        )

    signature = data[:HMAC_SIZE]
    payload = data[HMAC_SIZE:]
    expected = compute_hmac(key, payload)

    if hmac.compare_digest(signature, expected):
        return payload

    # Signature mismatch — could be a legacy file where the first 32 bytes
    # happen to exist but are not a valid HMAC.
    if data[0:1] == b"\x80":
        if allow_legacy:
            logger.warning(
                "Loading unsigned pickle file (legacy). "
                "Run 'manage.py resign_models' and set "
                "PICKLE_ALLOW_LEGACY_UNSIGNED=false."
            )
            return data
        raise ValueError(
            "Unsigned legacy pickle files are not allowed. "
            "Run 'manage.py resign_models' to sign existing files, "
            "then set PICKLE_ALLOW_LEGACY_UNSIGNED=false."
        )

    raise ValueError(
        "Pickle file signature verification failed. "
        "The file may have been tampered with."
    )
