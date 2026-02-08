"""Tests for FileDownloadRemoveMixin access control."""

from tempfile import NamedTemporaryFile

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from recotem.api.models import TrainingData

User = get_user_model()


@pytest.mark.django_db
def test_owner_can_download_file(client: Client, ml100k):
    """Test that project owner can download training data files."""
    user = User.objects.create_user(username="owner", password="pass")
    client.force_login(user)

    # Create project and upload data
    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="download_test_project", user_column="userId", item_column="movieId"),
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

    # Owner should be able to download the file
    download_url = reverse("training_data-download-file", args=[data_id])
    resp = client.get(download_url)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/octet-stream"
    assert "attachment" in resp["Content-Disposition"]


@pytest.mark.django_db
def test_owner_can_unlink_file(client: Client, ml100k):
    """Test that project owner can unlink training data files."""
    user = User.objects.create_user(username="owner", password="pass")
    client.force_login(user)

    # Create project and upload data
    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="unlink_test_project", user_column="userId", item_column="movieId"),
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

    # Owner should be able to unlink the file
    unlink_url = reverse("training_data-unlink-file", args=[data_id])
    resp = client.delete(unlink_url)
    assert resp.status_code == 200

    # Verify the file is unlinked
    data_obj = TrainingData.objects.get(id=data_id)
    assert not bool(data_obj.file)

    # Downloading after unlinking should return 404
    download_url = reverse("training_data-download-file", args=[data_id])
    resp = client.get(download_url)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_non_owner_cannot_download_file(client: Client, ml100k):
    """Test that non-owner cannot download files (gets 404 from queryset filtering)."""
    owner = User.objects.create_user(username="owner", password="pass")
    non_owner = User.objects.create_user(username="non_owner", password="pass")

    # Owner creates project and uploads data
    client.force_login(owner)
    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="owner_project", user_column="userId", item_column="movieId"),
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

    # Non-owner tries to download - should get 404 from get_object()
    client.force_login(non_owner)
    download_url = reverse("training_data-download-file", args=[data_id])
    resp = client.get(download_url)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_non_owner_cannot_unlink_file(client: Client, ml100k):
    """Test that non-owner cannot unlink files (gets 404 from queryset filtering)."""
    owner = User.objects.create_user(username="owner", password="pass")
    non_owner = User.objects.create_user(username="non_owner", password="pass")

    # Owner creates project and uploads data
    client.force_login(owner)
    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="owner_project_unlink", user_column="userId", item_column="movieId"),
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

    # Non-owner tries to unlink - should get 404 from get_object()
    client.force_login(non_owner)
    unlink_url = reverse("training_data-unlink-file", args=[data_id])
    resp = client.delete(unlink_url)
    assert resp.status_code == 404

    # Verify file is still linked
    data_obj = TrainingData.objects.get(id=data_id)
    assert bool(data_obj.file)


@pytest.mark.django_db
def test_unauthenticated_cannot_download_or_unlink(client: Client, ml100k):
    """Test that unauthenticated users cannot download or unlink files."""
    user = User.objects.create_user(username="user", password="pass")
    client.force_login(user)

    # Create project and upload data
    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="auth_test_project", user_column="userId", item_column="movieId"),
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

    # Logout and try to access
    client.logout()

    download_url = reverse("training_data-download-file", args=[data_id])
    resp = client.get(download_url)
    assert resp.status_code == 401

    unlink_url = reverse("training_data-unlink-file", args=[data_id])
    resp = client.delete(unlink_url)
    assert resp.status_code == 401


@pytest.mark.django_db
def test_item_metadata_file_access_control(client: Client, ml100k_item):
    """Test that FileDownloadRemoveMixin works for ItemMetaData as well."""
    owner = User.objects.create_user(username="owner", password="pass")
    non_owner = User.objects.create_user(username="non_owner", password="pass")

    # Owner creates project and uploads metadata
    client.force_login(owner)
    project_url = reverse("project-list")
    resp = client.post(
        project_url,
        dict(name="metadata_project", user_column="userId", item_column="movieId"),
    )
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    metadata_url = reverse("item_meta_data-list")
    json_file = NamedTemporaryFile(suffix=".json")
    ml100k_item.to_json(json_file)
    json_file.seek(0)

    resp = client.post(metadata_url, dict(project=project_id, file=json_file))
    assert resp.status_code == 201
    metadata_id = resp.json()["id"]

    # Owner can download
    download_url = reverse("item_meta_data-download-file", args=[metadata_id])
    resp = client.get(download_url)
    assert resp.status_code == 200

    # Non-owner cannot download
    client.force_login(non_owner)
    resp = client.get(download_url)
    assert resp.status_code == 404
