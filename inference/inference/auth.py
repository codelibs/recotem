"""API key authentication for the inference service."""

import logging
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import ApiKey

logger = logging.getLogger(__name__)

API_KEY_PREFIX = "rctm_"


def _check_password_django_compat(raw_key: str, hashed: str) -> bool:
    """Check password using Django's PBKDF2-SHA256 format.

    Django stores passwords as: algorithm$iterations$salt$hash
    Using passlib for compatibility.
    """
    from passlib.hash import django_pbkdf2_sha256

    try:
        return django_pbkdf2_sha256.verify(raw_key, hashed)
    except Exception:
        return False


def get_api_key(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> ApiKey:
    """FastAPI dependency to validate API key from X-API-Key header."""
    api_key_header = request.headers.get("x-api-key")
    if not api_key_header:
        raise HTTPException(status_code=401, detail="X-API-Key header required")

    if not api_key_header.startswith(API_KEY_PREFIX):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    random_part = api_key_header[len(API_KEY_PREFIX) :]
    if len(random_part) < 8:
        raise HTTPException(status_code=401, detail="Invalid API key format")

    prefix = random_part[:8]

    key_obj = (
        db.query(ApiKey)
        .filter(ApiKey.key_prefix == prefix, ApiKey.is_active.is_(True))
        .first()
    )

    if key_obj is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if key_obj.expires_at and key_obj.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=401, detail="API key expired")

    if not _check_password_django_compat(api_key_header, key_obj.hashed_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    return key_obj


def require_scope(scope: str):
    """FastAPI dependency factory that checks API key has a specific scope."""

    def _check(api_key: ApiKey = Depends(get_api_key)):  # noqa: B008
        if scope not in (api_key.scopes or []):
            raise HTTPException(
                status_code=403,
                detail=f"API key does not have '{scope}' scope",
            )
        return api_key

    return _check
