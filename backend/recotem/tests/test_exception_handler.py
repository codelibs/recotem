"""Tests for DRF standard error response format.

After removing the custom envelope wrapper, errors now use standard DRF format:
- 400: field errors as {"field_name": ["error message"]}
- 401: {"detail": "Authentication credentials were not provided."}
- 404: {"detail": "Not found."}
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

User = get_user_model()


@pytest.mark.django_db
def test_validation_error_standard_format(client: Client):
    """Test that field ValidationErrors return standard DRF format."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    project_url = reverse("project-list")
    resp = client.post(project_url, dict(name="incomplete_project"))

    assert resp.status_code == 400
    data = resp.json()
    # Standard DRF: field errors as dict
    assert "user_column" in data
    assert "item_column" in data


@pytest.mark.django_db
def test_not_found_standard_format(client: Client):
    """Test that 404 errors return standard DRF format."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    detail_url = reverse("project-detail", args=[99999])
    resp = client.get(detail_url)

    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data


@pytest.mark.django_db
def test_authentication_error_standard_format(client: Client):
    """Test that authentication errors return standard DRF format."""
    project_url = reverse("project-list")
    resp = client.get(project_url)

    assert resp.status_code in (401, 403)
    data = resp.json()
    assert "detail" in data


@pytest.mark.django_db
def test_field_errors_include_all_missing_fields(client: Client):
    """Test that multiple missing fields are all reported."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    project_url = reverse("project-list")
    resp = client.post(project_url, dict())

    assert resp.status_code == 400
    data = resp.json()
    assert "name" in data
    assert "user_column" in data
    assert "item_column" in data


@pytest.mark.django_db
def test_success_responses_not_wrapped(client: Client):
    """Test that successful responses return resource data directly."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="successful_project", user_column="userId", item_column="movieId"),
    )

    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["name"] == "successful_project"
