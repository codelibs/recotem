"""Pickle signing verification using the shared core module.

The pickle_signing_core module is copied from the backend to avoid
a Django dependency. It provides pure-Python HMAC-SHA256 operations.
"""

import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)

HMAC_SIZE = 32


def compute_hmac(key: bytes, payload: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def verify_and_extract(key: bytes, data: bytes, allow_legacy: bool = True) -> bytes:
    """Verify HMAC signature and return the pickle payload."""
    if len(data) <= HMAC_SIZE:
        if allow_legacy:
            logger.warning("Loading unsigned pickle file (legacy).")
            return data
        raise ValueError("Pickle file too short and legacy unsigned files not allowed.")

    signature = data[:HMAC_SIZE]
    payload = data[HMAC_SIZE:]
    expected = compute_hmac(key, payload)

    if hmac.compare_digest(signature, expected):
        return payload

    if data[0:1] == b"\x80":
        if allow_legacy:
            logger.warning("Loading unsigned pickle file (legacy).")
            return data
        raise ValueError("Unsigned legacy pickle files are not allowed.")

    raise ValueError("Pickle file signature verification failed.")
