"""Tests for user management API endpoints."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture(autouse=True)
def _use_locmem_cache(settings):
    """Use in-memory cache so tests work without Redis."""
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        username="admin",
        password="AdminPass123!",
        email="admin@example.com",
        is_staff=True,
    )


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        username="regular",
        password="RegularPass123!",
        email="regular@example.com",
        is_staff=False,
    )


@pytest.fixture
def admin_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def regular_client(regular_user):
    client = APIClient()
    client.force_authenticate(user=regular_user)
    return client


@pytest.fixture
def anon_client():
    return APIClient()


class TestUserList:
    def test_admin_can_list_users(self, admin_client, admin_user, regular_user):
        resp = admin_client.get("/api/v1/users/")
        assert resp.status_code == status.HTTP_200_OK
        # pagination_class=None returns a plain list
        usernames = [u["username"] for u in resp.data]
        assert admin_user.username in usernames
        assert regular_user.username in usernames

    def test_regular_user_cannot_list_users(self, regular_client):
        resp = regular_client.get("/api/v1/users/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_anonymous_cannot_list_users(self, anon_client):
        resp = anon_client.get("/api/v1/users/")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


class TestUserCreate:
    def test_admin_can_create_user(self, admin_client):
        resp = admin_client.post(
            "/api/v1/users/",
            {
                "username": "newuser",
                "email": "new@example.com",
                "password": "NewPass123!",
                "is_staff": False,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["username"] == "newuser"
        assert resp.data["is_active"] is True
        assert User.objects.filter(username="newuser").exists()

    def test_regular_user_cannot_create_user(self, regular_client):
        resp = regular_client.post(
            "/api/v1/users/",
            {
                "username": "newuser",
                "password": "NewPass123!",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


class TestUserDeactivateActivate:
    def test_admin_can_deactivate_user(self, admin_client, regular_user):
        resp = admin_client.post(f"/api/v1/users/{regular_user.pk}/deactivate/")
        assert resp.status_code == status.HTTP_200_OK
        regular_user.refresh_from_db()
        assert regular_user.is_active is False

    def test_admin_cannot_deactivate_self(self, admin_client, admin_user):
        resp = admin_client.post(f"/api/v1/users/{admin_user.pk}/deactivate/")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_admin_can_activate_user(self, admin_client, regular_user):
        regular_user.is_active = False
        regular_user.save()
        resp = admin_client.post(f"/api/v1/users/{regular_user.pk}/activate/")
        assert resp.status_code == status.HTTP_200_OK
        regular_user.refresh_from_db()
        assert regular_user.is_active is True


class TestPasswordReset:
    def test_admin_can_reset_password(self, admin_client, regular_user):
        resp = admin_client.post(
            f"/api/v1/users/{regular_user.pk}/reset_password/",
            {"new_password": "ResetPass123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        regular_user.refresh_from_db()
        assert regular_user.check_password("ResetPass123!")


class TestSelfPasswordChange:
    def test_user_can_change_own_password(self, regular_client, regular_user):
        resp = regular_client.post(
            "/api/v1/users/change_password/",
            {
                "old_password": "RegularPass123!",
                "new_password": "ChangedPass123!",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        regular_user.refresh_from_db()
        assert regular_user.check_password("ChangedPass123!")

    def test_wrong_old_password_rejected(self, regular_client):
        resp = regular_client.post(
            "/api/v1/users/change_password/",
            {
                "old_password": "WrongPassword123!",
                "new_password": "ChangedPass123!",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


class TestApiKeyBlocked:
    """API key authentication must not grant access to user management."""

    def test_api_key_cannot_list_users(self, admin_user, db):
        from recotem.api.authentication import generate_api_key
        from recotem.api.models import ApiKey, Project

        project = Project.objects.create(
            name="Test", user_column="u", item_column="i", owner=admin_user
        )
        full_key, prefix, hashed_key = generate_api_key()
        ApiKey.objects.create(
            project=project,
            owner=admin_user,
            name="test-key",
            key_prefix=prefix,
            hashed_key=hashed_key,
            scopes=["read", "write"],
        )
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=full_key)
        resp = client.get("/api/v1/users/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_api_key_cannot_change_password(self, regular_user, db):
        from recotem.api.authentication import generate_api_key
        from recotem.api.models import ApiKey, Project

        project = Project.objects.create(
            name="Test", user_column="u", item_column="i", owner=regular_user
        )
        full_key, prefix, hashed_key = generate_api_key()
        ApiKey.objects.create(
            project=project,
            owner=regular_user,
            name="test-key",
            key_prefix=prefix,
            hashed_key=hashed_key,
            scopes=["read", "write"],
        )
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=full_key)
        resp = client.post(
            "/api/v1/users/change_password/",
            {"old_password": "RegularPass123!", "new_password": "New123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


class TestAdminProjectVisibility:
    def test_admin_sees_all_projects(self, admin_client, regular_user):
        from recotem.api.models import Project

        project = Project.objects.create(
            name="Private Project",
            user_column="user",
            item_column="item",
            owner=regular_user,
        )
        resp = admin_client.get("/api/v1/project/")
        assert resp.status_code == status.HTTP_200_OK
        project_ids = [p["id"] for p in resp.data["results"]]
        assert project.id in project_ids
