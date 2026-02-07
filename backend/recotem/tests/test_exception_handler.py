"""Tests for custom exception handler envelope format."""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from rest_framework.exceptions import ValidationError, NotFound, PermissionDenied

from recotem.api.exceptions import (
    DataValidationError,
    ModelLoadError,
    ResourceNotFoundError,
    TuningJobError,
)


User = get_user_model()


@pytest.mark.django_db
def test_validation_error_envelope_format(client: Client):
    """Test that ValidationError is wrapped in the expected envelope format."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    # Trigger a validation error by creating a project with missing required fields
    project_url = reverse("project-list")
    resp = client.post(project_url, dict(name="incomplete_project"))

    assert resp.status_code == 400
    data = resp.json()

    # Verify envelope structure
    assert "success" in data
    assert data["success"] is False
    assert "error" in data
    assert "code" in data["error"]
    assert "detail" in data["error"]
    assert "data" in data
    assert data["data"] is None


@pytest.mark.django_db
def test_not_found_error_envelope_format(client: Client):
    """Test that NotFound error is wrapped in the expected envelope format."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    # Try to access a non-existent project
    detail_url = reverse("project-detail", args=[99999])
    resp = client.get(detail_url)

    assert resp.status_code == 404
    data = resp.json()

    # Verify envelope structure
    assert "success" in data
    assert data["success"] is False
    assert "error" in data
    assert "code" in data["error"]
    assert "detail" in data["error"]
    assert "data" in data
    assert data["data"] is None


@pytest.mark.django_db
def test_authentication_error_envelope_format(client: Client):
    """Test that authentication errors are wrapped in the expected envelope format."""
    # Try to access protected endpoint without authentication
    project_url = reverse("project-list")
    resp = client.get(project_url)

    assert resp.status_code == 401
    data = resp.json()

    # Verify envelope structure
    assert "success" in data
    assert data["success"] is False
    assert "error" in data
    assert "code" in data["error"]
    assert "detail" in data["error"]
    assert "data" in data
    assert data["data"] is None


@pytest.mark.django_db
def test_custom_exception_envelope_format(client: Client):
    """Test that custom Recotem exceptions are wrapped correctly."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    # Trigger a data validation error by uploading invalid data
    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="test_project", user_column="userId", item_column="movieId"),
    )
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    data_url = reverse("training_data-list")
    # Try to post without a file (should trigger validation error)
    resp = client.post(data_url, dict(project=project_id))

    assert resp.status_code == 400
    data = resp.json()

    # Verify envelope structure
    assert "success" in data
    assert data["success"] is False
    assert "error" in data
    assert "code" in data["error"]
    assert "detail" in data["error"]
    assert "data" in data
    assert data["data"] is None

    # Verify the detail message
    assert data["error"]["detail"][0] == "file is required."


@pytest.mark.django_db
def test_field_validation_error_envelope_format(client: Client):
    """Test that field-level validation errors are wrapped correctly."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    # Create a split config with invalid data
    split_url = reverse("split_config-list")
    resp = client.post(
        split_url,
        dict(heldout_ratio=1.5),  # Invalid: should be between 0 and 1
        content_type="application/json",
    )

    assert resp.status_code == 400
    data = resp.json()

    # Verify envelope structure
    assert "success" in data
    assert data["success"] is False
    assert "error" in data
    assert "code" in data["error"]
    assert "detail" in data["error"]
    assert "data" in data
    assert data["data"] is None


@pytest.mark.django_db
def test_error_detail_as_list(client: Client):
    """Test that error details can be a list of messages."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    # Create project with multiple missing required fields
    project_url = reverse("project-list")
    resp = client.post(project_url, dict())

    assert resp.status_code == 400
    data = resp.json()

    # Verify envelope structure
    assert "success" in data
    assert data["success"] is False
    assert "error" in data
    assert "detail" in data["error"]

    # Detail should contain multiple field errors
    detail = data["error"]["detail"]
    assert isinstance(detail, dict)  # DRF returns dict for field errors
    assert "name" in detail
    assert "user_column" in detail
    assert "item_column" in detail


@pytest.mark.django_db
def test_error_detail_as_string(client: Client):
    """Test that error details can be a simple string."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    # Try to access a non-existent resource
    detail_url = reverse("project-detail", args=[99999])
    resp = client.get(detail_url)

    assert resp.status_code == 404
    data = resp.json()

    # Verify envelope structure
    assert "success" in data
    assert data["success"] is False
    assert "error" in data
    assert "detail" in data["error"]

    # Detail can be a string or dict depending on the error type
    detail = data["error"]["detail"]
    assert detail is not None


@pytest.mark.django_db
def test_error_code_is_included(client: Client):
    """Test that error code is properly extracted and included."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    # Trigger various errors and verify their codes
    project_url = reverse("project-list")
    resp = client.post(project_url, dict(name="test"))

    assert resp.status_code == 400
    data = resp.json()

    # Verify code is present and meaningful
    assert "code" in data["error"]
    code = data["error"]["code"]
    assert code is not None
    # For field validation errors, code should be a dict with field names
    assert isinstance(code, (str, dict))


@pytest.mark.django_db
def test_success_responses_not_wrapped(client: Client):
    """Test that successful responses are not affected by the exception handler."""
    user = User.objects.create_user(username="test_user", password="pass")
    client.force_login(user)

    # Create a project successfully
    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="successful_project", user_column="userId", item_column="movieId"),
    )

    assert resp.status_code == 201
    data = resp.json()

    # Success responses should NOT have the error envelope
    assert "id" in data
    assert "name" in data
    assert data["name"] == "successful_project"
    # Should not have error envelope structure
    assert "success" not in data or data.get("success") is not False
