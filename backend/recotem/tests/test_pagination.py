import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from recotem.api.models import Project

User = get_user_model()


@pytest.mark.django_db
def test_project_list_is_paginated(client: Client):
    """Projects should be paginated with default page size."""
    user = User.objects.create_user(username="pagination_user", password="pass")
    client.force_login(user)

    for i in range(25):
        Project.objects.create(
            name=f"Project {i}",
            owner=user,
            user_column="uid",
            item_column="iid",
        )

    res = client.get(reverse("project-list"))
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 25
    assert len(data["results"]) == 20  # default page size
    assert data["next"] is not None


@pytest.mark.django_db
def test_project_list_page_2(client: Client):
    """Second page should contain remaining results."""
    user = User.objects.create_user(username="page2_user", password="pass")
    client.force_login(user)

    for i in range(25):
        Project.objects.create(
            name=f"Project {i}",
            owner=user,
            user_column="uid",
            item_column="iid",
        )

    res = client.get(reverse("project-list"), {"page": 2})
    assert res.status_code == 200
    data = res.json()
    assert len(data["results"]) == 5
    assert data["previous"] is not None
