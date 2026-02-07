"""Tests for permission-based access control across resources."""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from tempfile import NamedTemporaryFile

from recotem.api.models import Project, TrainingData


User = get_user_model()


@pytest.mark.django_db
def test_user_cannot_access_other_users_project(client: Client):
    """Test that User B cannot access User A's project."""
    # Create two users
    user_a = User.objects.create_user(username="user_a", password="pass_a")
    user_b = User.objects.create_user(username="user_b", password="pass_b")

    # User A creates a project
    client.force_login(user_a)
    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="user_a_project", user_column="userId", item_column="movieId"),
    )
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    # User B tries to access User A's project
    client.force_login(user_b)
    detail_url = reverse("project-detail", args=[project_id])
    resp = client.get(detail_url)
    assert resp.status_code == 404

    # User B cannot update User A's project
    resp = client.patch(detail_url, dict(name="hacked_name"), content_type="application/json")
    assert resp.status_code == 404

    # User B cannot delete User A's project
    resp = client.delete(detail_url)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_user_cannot_access_other_users_training_data(client: Client, ml100k):
    """Test that User B cannot access training data from User A's project."""
    user_a = User.objects.create_user(username="user_a", password="pass_a")
    user_b = User.objects.create_user(username="user_b", password="pass_b")

    # User A creates a project and uploads training data
    client.force_login(user_a)
    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="user_a_project_data", user_column="userId", item_column="movieId"),
    )
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    data_url = reverse("training_data-list")
    csv_file = NamedTemporaryFile(suffix=".csv")
    ml100k.to_csv(csv_file, index=False)
    csv_file.seek(0)

    resp = client.post(data_url, dict(project=project_id, file=csv_file))
    assert resp.status_code == 201
    data_id = resp.json()["id"]

    # User B tries to access User A's training data
    client.force_login(user_b)
    detail_url = reverse("training_data-detail", args=[data_id])
    resp = client.get(detail_url)
    assert resp.status_code == 404

    # User B should not see User A's data in the list
    resp = client.get(data_url)
    assert resp.status_code == 200
    results = resp.json()["results"]
    data_ids = [item["id"] for item in results]
    assert data_id not in data_ids


@pytest.mark.django_db
def test_unauthenticated_user_gets_401(client: Client):
    """Test that unauthenticated users get 401 for protected endpoints."""
    # Try to access project list without authentication
    project_url = reverse("project-list")
    resp = client.get(project_url)
    assert resp.status_code == 401

    # Try to create a project without authentication
    resp = client.post(
        project_url,
        dict(name="unauthorized_project", user_column="userId", item_column="movieId"),
    )
    assert resp.status_code == 401

    # Try to access training data list without authentication
    data_url = reverse("training_data-list")
    resp = client.get(data_url)
    assert resp.status_code == 401


@pytest.mark.django_db
def test_user_can_access_own_project(client: Client):
    """Test that users can access their own projects."""
    user = User.objects.create_user(username="user", password="pass")
    client.force_login(user)

    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="my_project", user_column="userId", item_column="movieId"),
    )
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    # User can retrieve their own project
    detail_url = reverse("project-detail", args=[project_id])
    resp = client.get(detail_url)
    assert resp.status_code == 200
    assert resp.json()["id"] == project_id

    # User can update their own project
    resp = client.patch(detail_url, dict(name="updated_project"), content_type="application/json")
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated_project"


@pytest.mark.django_db
def test_user_can_access_legacy_projects_without_owner(client: Client):
    """Test that users can access projects without an owner (backward compatibility)."""
    user = User.objects.create_user(username="user", password="pass")

    # Create a project without owner (simulating legacy data)
    project = Project.objects.create(
        name="legacy_project", user_column="userId", item_column="movieId", owner=None
    )

    client.force_login(user)
    detail_url = reverse("project-detail", args=[project.id])
    resp = client.get(detail_url)
    assert resp.status_code == 200
    assert resp.json()["id"] == project.id
