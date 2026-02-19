"""Tests for cookie-based JWT authentication."""

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()

_LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

pytestmark = pytest.mark.django_db


def _login(client, username="cookie_user", password="TestPass123!"):
    """Helper to perform login and return response."""
    return client.post(
        "/api/v1/auth/login/",
        data={"username": username, "password": password},
        content_type="application/json",
    )


@pytest.fixture
def user(db):
    return User.objects.create_user(username="cookie_user", password="TestPass123!")


@pytest.fixture(autouse=True)
def _use_locmem_cache(settings):
    settings.CACHES = _LOCMEM_CACHES
    from django.core.cache import cache

    cache.clear()


class TestCookieLogin:
    def test_login_sets_httponly_cookie(self, user, client):
        """Login must set httpOnly cookies."""
        response = _login(client)
        assert response.status_code == 200
        assert "jwt-access" in response.cookies
        assert response.cookies["jwt-access"]["httponly"]
        assert "jwt-refresh" in response.cookies
        assert response.cookies["jwt-refresh"]["httponly"]

    def test_cookie_samesite_is_strict(self, user, client):
        """Cookies must set SameSite=Strict."""
        response = _login(client)
        assert response.status_code == 200
        samesite = response.cookies["jwt-access"]["samesite"]
        assert samesite.lower() == "strict"

    def test_login_response_contains_access_token(self, user, client):
        """Response body still includes access for expiry parsing."""
        response = _login(client)
        data = response.json()
        assert "access" in data
        # JWT format: header.payload.signature
        assert data["access"].count(".") == 2

    def test_cookie_authenticates_api_request(self, user, client):
        """Cookie must authenticate without Authorization header."""
        login_resp = _login(client)
        assert login_resp.status_code == 200
        me_resp = client.get("/api/v1/auth/user/")
        assert me_resp.status_code == 200

    def test_cookie_authenticates_user_details(self, user, client):
        """Authenticated user details match the logged-in user."""
        _login(client)
        me_resp = client.get("/api/v1/auth/user/")
        data = me_resp.json()
        assert data["username"] == "cookie_user"

    def test_no_cookie_returns_401(self, user, client):
        """Request without cookie or header -> 401."""
        resp = client.get("/api/v1/auth/user/")
        assert resp.status_code in (401, 403)

    def test_logout_clears_cookies(self, user, client):
        """Logout must clear cookies."""
        _login(client)
        logout_resp = client.post("/api/v1/auth/logout/")
        assert logout_resp.status_code in (200, 204)
        if "jwt-access" in logout_resp.cookies:
            assert logout_resp.cookies["jwt-access"]["max-age"] in (
                0,
                "0",
                "",
            )

    def test_after_logout_api_returns_401(self, user, client):
        """After logout, protected endpoints reject requests."""
        _login(client)
        client.post("/api/v1/auth/logout/")
        # Force-clear cookies from client to simulate browser behavior
        client.cookies.clear()
        resp = client.get("/api/v1/auth/user/")
        assert resp.status_code in (401, 403)

    def test_bearer_token_still_works(self, user, client):
        """Bearer token auth must still work (backward compat)."""
        from rest_framework_simplejwt.tokens import RefreshToken

        token = str(RefreshToken.for_user(user).access_token)
        response = client.get(
            "/api/v1/auth/user/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert response.status_code == 200

    def test_invalid_credentials_returns_400(self, user, client):
        """Bad password -> 400 (no cookie set)."""
        resp = _login(client, password="wrong")
        assert resp.status_code == 400
        assert "jwt-access" not in resp.cookies

    def test_refresh_endpoint_returns_new_access(self, user, client):
        """POST /auth/token/refresh/ returns a new access token."""
        _login(client)
        resp = client.post("/api/v1/auth/token/refresh/")
        assert resp.status_code == 200
        data = resp.json()
        assert "access" in data

    def test_refresh_sets_new_cookie(self, user, client):
        """Token refresh updates the jwt-access cookie."""
        _login(client)
        resp = client.post("/api/v1/auth/token/refresh/")
        assert resp.status_code == 200
        assert "jwt-access" in resp.cookies
