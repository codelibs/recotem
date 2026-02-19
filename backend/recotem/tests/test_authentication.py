"""Tests for API key authentication middleware."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import RequestFactory
from rest_framework.exceptions import AuthenticationFailed

from recotem.api.authentication import (
    API_KEY_PREFIX,
    ApiKeyAuthentication,
    generate_api_key,
)
from recotem.api.models import ApiKey, Project

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="auth_user", password="pass")


@pytest.fixture
def project(user):
    return Project.objects.create(
        name="auth_project", user_column="u", item_column="i", owner=user
    )


@pytest.fixture
def api_key_data(user, project):
    full_key, prefix, hashed = generate_api_key()
    key_obj = ApiKey.objects.create(
        project=project,
        owner=user,
        name="test-key",
        key_prefix=prefix,
        hashed_key=hashed,
        scopes=["read", "predict"],
    )
    return full_key, key_obj


@pytest.mark.django_db
class TestGenerateApiKey:
    def test_returns_three_parts(self):
        full_key, prefix, hashed = generate_api_key()
        assert full_key.startswith(API_KEY_PREFIX)
        assert len(prefix) == 8
        assert hashed.startswith("pbkdf2_sha256$")

    def test_unique_keys(self):
        k1 = generate_api_key()[0]
        k2 = generate_api_key()[0]
        assert k1 != k2


@pytest.mark.django_db
class TestApiKeyAuthentication:
    def test_valid_key_authenticates(self, api_key_data, user):
        full_key, key_obj = api_key_data
        factory = RequestFactory()
        request = factory.get("/", HTTP_X_API_KEY=full_key)
        auth = ApiKeyAuthentication()
        result = auth.authenticate(request)
        assert result is not None
        auth_user, auth_key = result
        assert auth_user == user
        assert auth_key == key_obj

    def test_no_header_returns_none(self):
        factory = RequestFactory()
        request = factory.get("/")
        auth = ApiKeyAuthentication()
        assert auth.authenticate(request) is None

    def test_wrong_prefix_returns_none(self):
        factory = RequestFactory()
        request = factory.get("/", HTTP_X_API_KEY="wrong_prefix_abc123")
        auth = ApiKeyAuthentication()
        assert auth.authenticate(request) is None

    def test_invalid_key_raises(self, api_key_data):
        from rest_framework.exceptions import AuthenticationFailed

        factory = RequestFactory()
        request = factory.get("/", HTTP_X_API_KEY=f"{API_KEY_PREFIX}wrongkey12345678")
        auth = ApiKeyAuthentication()
        with pytest.raises(AuthenticationFailed):
            auth.authenticate(request)

    def test_short_key_raises(self):
        from rest_framework.exceptions import AuthenticationFailed

        factory = RequestFactory()
        request = factory.get("/", HTTP_X_API_KEY=f"{API_KEY_PREFIX}short")
        auth = ApiKeyAuthentication()
        with pytest.raises(AuthenticationFailed, match="Invalid API key format"):
            auth.authenticate(request)

    def test_revoked_key_fails(self, api_key_data):
        from rest_framework.exceptions import AuthenticationFailed

        full_key, key_obj = api_key_data
        key_obj.is_active = False
        key_obj.save()

        factory = RequestFactory()
        request = factory.get("/", HTTP_X_API_KEY=full_key)
        auth = ApiKeyAuthentication()
        with pytest.raises(AuthenticationFailed):
            auth.authenticate(request)

    def test_expired_key_fails(self, api_key_data):
        from datetime import timedelta

        from django.utils import timezone
        from rest_framework.exceptions import AuthenticationFailed

        full_key, key_obj = api_key_data
        key_obj.expires_at = timezone.now() - timedelta(hours=1)
        key_obj.save()

        factory = RequestFactory()
        request = factory.get("/", HTTP_X_API_KEY=full_key)
        auth = ApiKeyAuthentication()
        with pytest.raises(AuthenticationFailed, match="expired"):
            auth.authenticate(request)

    def test_last_used_at_updated(self, api_key_data):
        full_key, key_obj = api_key_data
        assert key_obj.last_used_at is None

        factory = RequestFactory()
        request = factory.get("/", HTTP_X_API_KEY=full_key)
        auth = ApiKeyAuthentication()
        auth.authenticate(request)

        key_obj.refresh_from_db()
        assert key_obj.last_used_at is not None

    def test_authenticate_header(self):
        auth = ApiKeyAuthentication()
        factory = RequestFactory()
        request = factory.get("/")
        assert auth.authenticate_header(request) == "X-API-Key"


@pytest.mark.django_db
class TestRequireManagementScope:
    """Test RequireManagementScope permission class."""

    def test_read_scope_for_get(self, api_key_data, user):
        """GET requires 'read' scope."""
        from recotem.api.authentication import RequireManagementScope

        full_key, key_obj = api_key_data
        # key_obj has scopes=["read", "predict"]
        factory = RequestFactory()
        request = factory.get("/")
        request.api_key = key_obj
        request.user = user
        perm = RequireManagementScope()
        assert perm.has_permission(request, None) is True

    def test_write_scope_for_post(self, api_key_data, user):
        """POST requires 'write' scope -- key only has read+predict, should fail."""
        from recotem.api.authentication import RequireManagementScope

        full_key, key_obj = api_key_data
        factory = RequestFactory()
        request = factory.post("/")
        request.api_key = key_obj
        request.user = user
        perm = RequireManagementScope()
        assert perm.has_permission(request, None) is False

    def test_jwt_always_allowed(self, user):
        """JWT user (no api_key attr) passes all scope checks."""
        from recotem.api.authentication import RequireManagementScope

        factory = RequestFactory()
        request = factory.post("/")
        request.user = user
        # No api_key attribute -> JWT
        perm = RequireManagementScope()
        assert perm.has_permission(request, None) is True


@pytest.mark.django_db
class TestAmbiguousApiKeyPrefix:
    def test_ambiguous_prefix(self, user, project):
        """Two keys with same prefix -> 'Ambiguous API key prefix'."""
        # Create two keys with the same prefix
        prefix = "SAMEPRFX"
        ApiKey.objects.create(
            project=project,
            owner=user,
            name="key1",
            key_prefix=prefix,
            hashed_key=make_password("dummy1"),
            scopes=["read"],
        )
        ApiKey.objects.create(
            project=project,
            owner=user,
            name="key2",
            key_prefix=prefix,
            hashed_key=make_password("dummy2"),
            scopes=["read"],
        )

        factory = RequestFactory()
        request = factory.get(
            "/", HTTP_X_API_KEY=f"{API_KEY_PREFIX}{prefix}longenoughkey"
        )
        auth = ApiKeyAuthentication()
        with pytest.raises(AuthenticationFailed, match="Ambiguous"):
            auth.authenticate(request)
