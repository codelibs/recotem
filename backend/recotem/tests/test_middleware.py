"""Tests for JwtAuthMiddleware — cookie-based and query-param fallback."""

from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import RefreshToken

from recotem.api.middleware import JwtAuthMiddleware, get_user_from_token

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="ws_user", password="pass")


@pytest.fixture
def access_token(user):
    return str(RefreshToken.for_user(user).access_token)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestGetUserFromToken:
    async def test_valid_token_returns_user(self, user, access_token):
        result = await get_user_from_token(access_token)
        assert result == user

    async def test_invalid_token_returns_anonymous(self):
        result = await get_user_from_token("not-a-jwt")
        assert isinstance(result, AnonymousUser)

    async def test_empty_string_returns_anonymous(self):
        result = await get_user_from_token("")
        assert isinstance(result, AnonymousUser)

    async def test_expired_token_returns_anonymous(self, user):
        """A token with exp in the past must return AnonymousUser."""
        from datetime import timedelta

        from rest_framework_simplejwt.tokens import AccessToken

        token = AccessToken.for_user(user)
        token.set_exp(lifetime=-timedelta(seconds=1))
        result = await get_user_from_token(str(token))
        assert isinstance(result, AnonymousUser)

    async def test_deleted_user_returns_anonymous(self, user, access_token):
        """Token for a user that no longer exists."""
        from channels.db import database_sync_to_async

        await database_sync_to_async(user.delete)()
        result = await get_user_from_token(access_token)
        assert isinstance(result, AnonymousUser)

    async def test_token_with_nonexistent_user_id(self):
        """Token with a user_id that was never in the DB."""
        from rest_framework_simplejwt.tokens import AccessToken

        token = AccessToken()
        token["user_id"] = 999999
        result = await get_user_from_token(str(token))
        assert isinstance(result, AnonymousUser)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestJwtAuthMiddleware:
    def _make_middleware(self):
        inner = AsyncMock(return_value=None)
        return JwtAuthMiddleware(inner), inner

    async def test_cookie_auth_sets_user(self, user, access_token):
        """Cookie header takes priority for authentication."""
        middleware, _inner = self._make_middleware()
        scope = {
            "type": "websocket",
            "headers": [
                (b"cookie", f"jwt-access={access_token}".encode()),
            ],
            "query_string": b"",
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert scope["user"] == user

    async def test_no_cookie_no_token_is_anonymous(self):
        """No cookie and no query param -> AnonymousUser."""
        middleware, _inner = self._make_middleware()
        scope = {
            "type": "websocket",
            "headers": [],
            "query_string": b"",
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert isinstance(scope["user"], AnonymousUser)

    async def test_invalid_cookie_is_anonymous(self):
        middleware, _inner = self._make_middleware()
        scope = {
            "type": "websocket",
            "headers": [
                (b"cookie", b"jwt-access=invalid.jwt.token"),
            ],
            "query_string": b"",
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert isinstance(scope["user"], AnonymousUser)

    # --- Query param fallback ---

    async def test_query_param_fallback(self, user, access_token):
        """?token= query param authenticates when no cookie."""
        middleware, _inner = self._make_middleware()
        qs = urlencode({"token": access_token}).encode()
        scope = {
            "type": "websocket",
            "headers": [],
            "query_string": qs,
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert scope["user"] == user

    async def test_invalid_query_param_is_anonymous(self):
        """Invalid JWT in query param -> AnonymousUser."""
        middleware, _inner = self._make_middleware()
        scope = {
            "type": "websocket",
            "headers": [],
            "query_string": b"token=bad-jwt",
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert isinstance(scope["user"], AnonymousUser)

    async def test_empty_query_param_is_anonymous(self):
        """?token= (empty value) -> AnonymousUser."""
        middleware, _inner = self._make_middleware()
        scope = {
            "type": "websocket",
            "headers": [],
            "query_string": b"token=",
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert isinstance(scope["user"], AnonymousUser)

    # --- Cookie vs query param precedence ---

    async def test_cookie_takes_precedence_over_query_param(self, user, access_token):
        """Cookie wins when both cookie and query param present."""
        middleware, _inner = self._make_middleware()
        scope = {
            "type": "websocket",
            "headers": [
                (
                    b"cookie",
                    f"jwt-access={access_token}".encode(),
                ),
            ],
            "query_string": b"token=bad-jwt",
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert scope["user"] == user

    async def test_invalid_cookie_does_not_fall_through_to_query(
        self, user, access_token
    ):
        """An invalid cookie does NOT fall through to query param.

        When the cookie is present (even if the JWT inside is invalid),
        the middleware should not try the query param because the
        cookie morsel exists.
        """
        middleware, _inner = self._make_middleware()
        qs = urlencode({"token": access_token}).encode()
        scope = {
            "type": "websocket",
            "headers": [
                (b"cookie", b"jwt-access=invalid.jwt"),
            ],
            "query_string": qs,
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        # Cookie was present so query param is NOT consulted
        assert isinstance(scope["user"], AnonymousUser)

    # --- Multiple cookies ---

    async def test_multiple_cookies_picks_jwt_access(self, user, access_token):
        """Multiple cookies in header; jwt-access is still found."""
        middleware, _inner = self._make_middleware()
        cookie_str = f"session=abc123; jwt-access={access_token}; csrftoken=x"
        scope = {
            "type": "websocket",
            "headers": [
                (b"cookie", cookie_str.encode()),
            ],
            "query_string": b"",
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert scope["user"] == user

    async def test_other_cookies_without_jwt_access(self):
        """Other cookies present but no jwt-access -> AnonymousUser."""
        middleware, _inner = self._make_middleware()
        scope = {
            "type": "websocket",
            "headers": [
                (b"cookie", b"session=abc123; csrftoken=x"),
            ],
            "query_string": b"",
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert isinstance(scope["user"], AnonymousUser)

    # --- Scope edge cases ---

    async def test_missing_headers_key(self):
        """Scope without 'headers' key -> AnonymousUser."""
        middleware, _inner = self._make_middleware()
        scope = {
            "type": "websocket",
            "query_string": b"",
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert isinstance(scope["user"], AnonymousUser)

    async def test_missing_query_string_key(self):
        """Scope without 'query_string' key -> AnonymousUser."""
        middleware, _inner = self._make_middleware()
        scope = {
            "type": "websocket",
            "headers": [],
        }
        await middleware(scope, AsyncMock(), AsyncMock())
        assert isinstance(scope["user"], AnonymousUser)

    async def test_inner_app_is_called(self, access_token, user):
        """Middleware must always call the inner application."""
        middleware, inner = self._make_middleware()
        scope = {
            "type": "websocket",
            "headers": [
                (b"cookie", f"jwt-access={access_token}".encode()),
            ],
            "query_string": b"",
        }
        receive, send = AsyncMock(), AsyncMock()
        await middleware(scope, receive, send)
        inner.assert_called_once()
