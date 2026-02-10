import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse

from recotem.api.models import Project

User = get_user_model()


@pytest.mark.django_db
def test_same_owner_cannot_have_duplicate_project_names(client: Client):
    """Same user cannot create two projects with the same name."""
    user = User.objects.create_user(username="unique_test_user", password="pass")
    client.force_login(user)

    res1 = client.post(
        reverse("project-list"),
        {"name": "My Project", "user_column": "uid", "item_column": "iid"},
        content_type="application/json",
    )
    assert res1.status_code == 201

    res2 = client.post(
        reverse("project-list"),
        {"name": "My Project", "user_column": "uid", "item_column": "iid"},
        content_type="application/json",
    )
    assert res2.status_code == 400


@pytest.mark.django_db
def test_different_owners_can_have_same_project_name(client: Client):
    """Different users can create projects with the same name."""
    user_a = User.objects.create_user(username="owner_a", password="pass")
    user_b = User.objects.create_user(username="owner_b", password="pass")

    client.force_login(user_a)
    res1 = client.post(
        reverse("project-list"),
        {"name": "Shared Name", "user_column": "uid", "item_column": "iid"},
        content_type="application/json",
    )
    assert res1.status_code == 201

    client.force_login(user_b)
    res2 = client.post(
        reverse("project-list"),
        {"name": "Shared Name", "user_column": "uid", "item_column": "iid"},
        content_type="application/json",
    )
    assert res2.status_code == 201


@pytest.mark.django_db
def test_db_constraint_prevents_duplicate_per_owner():
    """Database constraint enforces uniqueness at DB level."""
    user = User.objects.create_user(username="constraint_user", password="pass")
    Project.objects.create(
        name="Constrained", owner=user, user_column="u", item_column="i"
    )
    with pytest.raises(IntegrityError):
        Project.objects.create(
            name="Constrained", owner=user, user_column="u", item_column="i"
        )


@pytest.mark.django_db
def test_update_project_name_to_existing_fails(client: Client):
    """Updating to an existing name for the same owner should fail."""
    user = User.objects.create_user(username="rename_user", password="pass")
    client.force_login(user)

    client.post(
        reverse("project-list"),
        {"name": "Project A", "user_column": "uid", "item_column": "iid"},
        content_type="application/json",
    )
    res_b = client.post(
        reverse("project-list"),
        {"name": "Project B", "user_column": "uid", "item_column": "iid"},
        content_type="application/json",
    )
    project_b_id = res_b.json()["id"]

    res = client.patch(
        reverse("project-detail", args=[project_b_id]),
        {"name": "Project A"},
        content_type="application/json",
    )
    assert res.status_code == 400
