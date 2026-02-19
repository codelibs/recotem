"""JWT authentication middleware for Django Channels WebSocket connections.

Reads the JWT access token from the ``jwt-access`` httpOnly cookie sent
automatically by the browser on WebSocket upgrade. Falls back to the legacy
``?token=`` query parameter for backward compatibility.
"""

from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def get_user_from_token(token_str):
    """Validate a JWT access token and return the corresponding user."""
    try:
        token = AccessToken(token_str)
        User = get_user_model()
        return User.objects.get(id=token["user_id"])
    except Exception:
        return AnonymousUser()


class JwtAuthMiddleware(BaseMiddleware):
    """Authenticate WebSocket connections via the jwt-access httpOnly cookie."""

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        cookie_header = headers.get(b"cookie", b"").decode()
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        jwt_morsel = cookie.get("jwt-access")
        token = jwt_morsel.value if jwt_morsel else None

        if not token:
            query_string = scope.get("query_string", b"").decode()
            params = parse_qs(query_string)
            token_list = params.get("token", [])
            token = token_list[0] if token_list else None

        scope["user"] = await get_user_from_token(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)
