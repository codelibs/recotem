"""Tests for ApiKeyViewSet CRUD operations."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from recotem.api.models import ApiKey, Project

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="key_user", password="pass")


@pytest.fixture
def project(user):
    return Project.objects.create(
        name="key_project", user_column="u", item_column="i", owner=user
    )


@pytest.fixture
def auth_client(client: Client, user):
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestApiKeyViewSet:
    """Tests for API key CRUD and revoke."""

    def test_create_api_key(self, auth_client, project):
        url = reverse("api_key-list")
        resp = auth_client.post(
            url,
            {"project": project.id, "name": "test-key", "scopes": ["read", "predict"]},
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test-key"
        assert "key" in data
        assert data["key"].startswith("rctm_")

    def test_create_api_key_invalid_scope(self, auth_client, project):
        url = reverse("api_key-list")
        resp = auth_client.post(
            url,
            {"project": project.id, "name": "bad-key", "scopes": ["invalid"]},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_create_api_key_empty_scopes(self, auth_client, project):
        url = reverse("api_key-list")
        resp = auth_client.post(
            url,
            {"project": project.id, "name": "empty-scope", "scopes": []},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_list_api_keys(self, auth_client, project):
        url = reverse("api_key-list")
        # Create two keys
        auth_client.post(
            url,
            {"project": project.id, "name": "key-1", "scopes": ["read"]},
            content_type="application/json",
        )
        auth_client.post(
            url,
            {"project": project.id, "name": "key-2", "scopes": ["write"]},
            content_type="application/json",
        )
        resp = auth_client.get(url)
        assert resp.status_code == 200
        names = [k["name"] for k in resp.json()["results"]]
        assert "key-1" in names
        assert "key-2" in names

    def test_revoke_api_key(self, auth_client, project):
        # Create a key
        url = reverse("api_key-list")
        create_resp = auth_client.post(
            url,
            {"project": project.id, "name": "revoke-me", "scopes": ["read"]},
            content_type="application/json",
        )
        key_id = create_resp.json()["id"]

        # Revoke it
        revoke_url = reverse("api_key-revoke", args=[key_id])
        resp = auth_client.post(revoke_url)
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

        # Verify it's deactivated
        key = ApiKey.objects.get(id=key_id)
        assert key.is_active is False

    def test_delete_api_key(self, auth_client, project):
        url = reverse("api_key-list")
        create_resp = auth_client.post(
            url,
            {"project": project.id, "name": "delete-me", "scopes": ["read"]},
            content_type="application/json",
        )
        key_id = create_resp.json()["id"]

        detail_url = reverse("api_key-detail", args=[key_id])
        resp = auth_client.delete(detail_url)
        assert resp.status_code == 204
        assert not ApiKey.objects.filter(id=key_id).exists()

    def test_unauthenticated_cannot_list_keys(self, client: Client):
        url = reverse("api_key-list")
        resp = client.get(url)
        assert resp.status_code == 401

    def test_filter_by_project(self, auth_client, user):
        p1 = Project.objects.create(
            name="p1", user_column="u", item_column="i", owner=user
        )
        p2 = Project.objects.create(
            name="p2", user_column="u", item_column="i", owner=user
        )

        url = reverse("api_key-list")
        auth_client.post(
            url,
            {"project": p1.id, "name": "k1", "scopes": ["read"]},
            content_type="application/json",
        )
        auth_client.post(
            url,
            {"project": p2.id, "name": "k2", "scopes": ["read"]},
            content_type="application/json",
        )

        resp = auth_client.get(url, {"project": p1.id})
        assert resp.status_code == 200
        names = [k["name"] for k in resp.json()["results"]]
        assert "k1" in names
        assert "k2" not in names


@pytest.mark.django_db
class TestApiKeyOwnershipIsolation:
    """Users should only see their own project's API keys."""

    def test_user_cannot_see_other_users_keys(self, client: Client):
        user_a = User.objects.create_user(username="a", password="pass")
        user_b = User.objects.create_user(username="b", password="pass")
        project_a = Project.objects.create(
            name="pa", user_column="u", item_column="i", owner=user_a
        )

        url = reverse("api_key-list")

        client.force_login(user_a)
        client.post(
            url,
            {"project": project_a.id, "name": "secret-key", "scopes": ["read"]},
            content_type="application/json",
        )

        client.force_login(user_b)
        resp = client.get(url)
        assert resp.status_code == 200
        names = [k["name"] for k in resp.json()["results"]]
        assert "secret-key" not in names

    def test_user_cannot_create_key_for_other_project(self, client: Client):
        user_a = User.objects.create_user(username="a", password="pass")
        user_b = User.objects.create_user(username="b", password="pass")
        project_a = Project.objects.create(
            name="pa", user_column="u", item_column="i", owner=user_a
        )

        url = reverse("api_key-list")
        client.force_login(user_b)
        resp = client.post(
            url,
            {"project": project_a.id, "name": "stolen-key", "scopes": ["read"]},
            content_type="application/json",
        )
        assert resp.status_code == 400
