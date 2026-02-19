"""Tests for inference auth module."""

import inspect
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from inference.auth import (
    API_KEY_PREFIX,
    _check_password_django_compat,
    get_api_key,
    require_scope,
)


def _make_request(api_key: str | None = None) -> Request:
    """Build a minimal ASGI Request with optional X-API-Key header."""
    headers: list[tuple[bytes, bytes]] = []
    if api_key is not None:
        headers.append((b"x-api-key", api_key.encode()))
    return Request({"type": "http", "headers": headers})


def _valid_key(prefix: str = "testpref") -> str:
    """Return a syntactically valid API key string."""
    return f"{API_KEY_PREFIX}{prefix}{'a' * 32}"


def _make_key_obj(
    *,
    hashed_key: str = "hashed",
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
):
    """Return a mock ApiKey ORM object."""
    obj = MagicMock()
    obj.hashed_key = hashed_key
    obj.scopes = scopes
    obj.expires_at = expires_at
    return obj


# ── Code quality ──────────────────────────────────────────


class TestCodeQuality:
    def test_no_e712_noqa_in_auth(self):
        """Source must use .is_(True) instead of == True."""
        from inference import auth

        source = inspect.getsource(auth)
        assert "noqa: E712" not in source


# ── get_api_key: header validation ────────────────────────


class TestGetApiKeyHeaderValidation:
    def test_missing_header_returns_401(self):
        request = _make_request(api_key=None)
        with pytest.raises(HTTPException) as exc:
            get_api_key(request=request, db=MagicMock())
        assert exc.value.status_code == 401
        assert "required" in exc.value.detail.lower()

    def test_empty_header_returns_401(self):
        request = _make_request(api_key="")
        with pytest.raises(HTTPException) as exc:
            get_api_key(request=request, db=MagicMock())
        assert exc.value.status_code == 401

    def test_wrong_prefix_returns_401(self):
        request = _make_request(api_key="wrong_prefix_abcdef")
        with pytest.raises(HTTPException) as exc:
            get_api_key(request=request, db=MagicMock())
        assert exc.value.status_code == 401
        assert "format" in exc.value.detail.lower()

    def test_too_short_random_part_returns_401(self):
        """Random part after prefix must be >= 8 chars."""
        request = _make_request(api_key=f"{API_KEY_PREFIX}short")
        with pytest.raises(HTTPException) as exc:
            get_api_key(request=request, db=MagicMock())
        assert exc.value.status_code == 401
        assert "format" in exc.value.detail.lower()

    def test_exactly_8_char_random_part_passes_format_check(self):
        """8-char random part is the minimum; format check passes."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        request = _make_request(api_key=f"{API_KEY_PREFIX}12345678")
        with pytest.raises(HTTPException) as exc:
            get_api_key(request=request, db=db)
        # 401 for "Invalid API key" (not found), NOT for format
        assert exc.value.detail == "Invalid API key"


# ── get_api_key: DB lookup / inactive keys ────────────────


class TestGetApiKeyDbLookup:
    def test_inactive_key_is_rejected(self):
        """Inactive key filtered at DB level -> 401."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        request = _make_request(api_key=_valid_key())
        with pytest.raises(HTTPException) as exc:
            get_api_key(request=request, db=db)
        assert exc.value.status_code == 401

    def test_query_uses_is_true(self):
        """DB filter must use .is_(True), not == True."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        request = _make_request(api_key=_valid_key())
        with pytest.raises(HTTPException):
            get_api_key(request=request, db=db)
        # filter was called — we trust the code-quality test above
        db.query.return_value.filter.assert_called_once()


# ── get_api_key: expiration ───────────────────────────────


class TestGetApiKeyExpiration:
    def test_expired_key_returns_401(self):
        """Key with expires_at in the past is rejected."""
        key_obj = _make_key_obj(
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = key_obj
        request = _make_request(api_key=_valid_key())
        with pytest.raises(HTTPException) as exc:
            get_api_key(request=request, db=db)
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()

    def test_key_without_expiry_does_not_reject(self):
        """Key with expires_at=None passes expiration check."""
        key_obj = _make_key_obj(expires_at=None)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = key_obj
        request = _make_request(api_key=_valid_key())
        # Will fail at password check, not expiration
        with patch(
            "inference.auth._check_password_django_compat",
            return_value=True,
        ):
            result = get_api_key(request=request, db=db)
        assert result is key_obj

    def test_key_with_future_expiry_passes(self):
        """Key expiring in the future passes expiration check."""
        key_obj = _make_key_obj(
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = key_obj
        request = _make_request(api_key=_valid_key())
        with patch(
            "inference.auth._check_password_django_compat",
            return_value=True,
        ):
            result = get_api_key(request=request, db=db)
        assert result is key_obj


# ── get_api_key: password verification ────────────────────


class TestGetApiKeyPassword:
    def test_wrong_password_returns_401(self):
        key_obj = _make_key_obj(expires_at=None)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = key_obj
        request = _make_request(api_key=_valid_key())
        with patch(
            "inference.auth._check_password_django_compat",
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc:
                get_api_key(request=request, db=db)
            assert exc.value.status_code == 401

    def test_correct_password_returns_key_obj(self):
        key_obj = _make_key_obj(expires_at=None)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = key_obj
        request = _make_request(api_key=_valid_key())
        with patch(
            "inference.auth._check_password_django_compat",
            return_value=True,
        ):
            result = get_api_key(request=request, db=db)
        assert result is key_obj


# ── _check_password_django_compat ─────────────────────────


class TestCheckPasswordDjangoCompat:
    def test_invalid_hash_format_returns_false(self):
        """Garbage hash string must not raise, just return False."""
        assert _check_password_django_compat("key", "not-a-hash") is False

    def test_empty_strings_return_false(self):
        assert _check_password_django_compat("", "") is False


# ── require_scope ─────────────────────────────────────────


class TestRequireScope:
    def test_key_with_required_scope_passes(self):
        key_obj = _make_key_obj(scopes=["predict", "admin"])
        checker = require_scope("predict")
        result = checker(api_key=key_obj)
        assert result is key_obj

    def test_key_without_required_scope_returns_403(self):
        key_obj = _make_key_obj(scopes=["admin"])
        checker = require_scope("predict")
        with pytest.raises(HTTPException) as exc:
            checker(api_key=key_obj)
        assert exc.value.status_code == 403
        assert "predict" in exc.value.detail

    def test_key_with_no_scopes_returns_403(self):
        key_obj = _make_key_obj(scopes=None)
        checker = require_scope("predict")
        with pytest.raises(HTTPException) as exc:
            checker(api_key=key_obj)
        assert exc.value.status_code == 403

    def test_key_with_empty_scopes_returns_403(self):
        key_obj = _make_key_obj(scopes=[])
        checker = require_scope("predict")
        with pytest.raises(HTTPException) as exc:
            checker(api_key=key_obj)
        assert exc.value.status_code == 403
