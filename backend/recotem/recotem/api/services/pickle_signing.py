"""HMAC-SHA256 signing and verification for pickle files.

Pickle deserialization is inherently unsafe because it can execute arbitrary code.
This module adds integrity verification to ensure that only pickle files generated
by this application (signed with SECRET_KEY) are loaded.

File format: HMAC_SIGNATURE (32 bytes) + PICKLE_PAYLOAD (remaining bytes)
"""

from django.conf import settings

from . import pickle_signing_core

HMAC_SIZE = pickle_signing_core.HMAC_SIZE


def _get_hmac_key() -> bytes:
    return settings.SECRET_KEY.encode("utf-8")


def _compute_hmac(payload: bytes) -> bytes:
    return pickle_signing_core.compute_hmac(_get_hmac_key(), payload)


def sign_pickle_bytes(payload: bytes) -> bytes:
    """Prepend HMAC-SHA256 signature to pickle payload."""
    return pickle_signing_core.sign_pickle_bytes(_get_hmac_key(), payload)


def _allow_legacy() -> bool:
    return getattr(settings, "PICKLE_ALLOW_LEGACY_UNSIGNED", True)


def verify_and_extract(data: bytes) -> bytes:
    """Verify HMAC signature and return the pickle payload.

    For unsigned legacy files (pre-signing migration), the data is returned
    as-is and a warning is logged — but only when PICKLE_ALLOW_LEGACY_UNSIGNED
    is True. Set it to False after running ``resign_models`` to reject unsigned files.

    Raises ValueError if the signature is present but invalid.
    """
    return pickle_signing_core.verify_and_extract(
        _get_hmac_key(), data, _allow_legacy()
    )
