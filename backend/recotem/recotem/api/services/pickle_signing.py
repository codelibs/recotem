"""HMAC-SHA256 signing and verification for pickle files.

Pickle deserialization is inherently unsafe because it can execute arbitrary code.
This module adds integrity verification to ensure that only pickle files generated
by this application (signed with SECRET_KEY) are loaded.

File format: HMAC_SIGNATURE (32 bytes) + PICKLE_PAYLOAD (remaining bytes)
"""

import hashlib
import hmac
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

HMAC_SIZE = 32  # SHA-256 digest size


def _get_hmac_key() -> bytes:
    return settings.SECRET_KEY.encode("utf-8")


def _compute_hmac(payload: bytes) -> bytes:
    return hmac.new(_get_hmac_key(), payload, hashlib.sha256).digest()


def sign_pickle_bytes(payload: bytes) -> bytes:
    """Prepend HMAC-SHA256 signature to pickle payload."""
    signature = _compute_hmac(payload)
    return signature + payload


def _allow_legacy() -> bool:
    return getattr(settings, "PICKLE_ALLOW_LEGACY_UNSIGNED", True)


def verify_and_extract(data: bytes) -> bytes:
    """Verify HMAC signature and return the pickle payload.

    For unsigned legacy files (pre-signing migration), the data is returned
    as-is and a warning is logged — but only when PICKLE_ALLOW_LEGACY_UNSIGNED
    is True. Set it to False after running ``resign_models`` to reject unsigned files.

    Raises ValueError if the signature is present but invalid.
    """
    if len(data) <= HMAC_SIZE:
        if _allow_legacy():
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
    expected = _compute_hmac(payload)

    if hmac.compare_digest(signature, expected):
        return payload

    # Signature mismatch — could be a legacy file where the first 32 bytes
    # happen to exist but are not a valid HMAC. Try treating the entire blob
    # as an unsigned pickle (pickle protocol starts with 0x80 for protocol 2+).
    if data[0:1] == b"\x80":
        if _allow_legacy():
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
