import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from recotem.api.models import ModelConfiguration, Project

User = get_user_model()


@pytest.mark.django_db
def test_create_model_configuration(client: Client):
    """Creating a model configuration should succeed."""
    user = User.objects.create_user(username="config_user", password="pass")
    project = Project.objects.create(
        name="Config Test", owner=user, user_column="uid", item_column="iid"
    )
    client.force_login(user)

    res = client.post(
        reverse("model_configuration-list"),
        {
            "project": project.id,
            "recommender_class_name": "IALSRecommender",
            "parameters_json": '{"n_components": 64}',
        },
        content_type="application/json",
    )
    assert res.status_code == 201
    assert res.json()["recommender_class_name"] == "IALSRecommender"


@pytest.mark.django_db
def test_list_model_configurations_filtered_by_project(client: Client):
    """Configurations should be filterable by project."""
    user = User.objects.create_user(username="config_filter_user", password="pass")
    p1 = Project.objects.create(name="P1", owner=user, user_column="u", item_column="i")
    p2 = Project.objects.create(name="P2", owner=user, user_column="u", item_column="i")

    ModelConfiguration.objects.create(
        project=p1, recommender_class_name="IALS", parameters_json="{}"
    )
    ModelConfiguration.objects.create(
        project=p2, recommender_class_name="P3", parameters_json="{}"
    )

    client.force_login(user)
    res = client.get(reverse("model_configuration-list"), {"project": p1.id})
    assert res.status_code == 200
    results = res.json().get("results", res.json())
    assert len(results) == 1
    assert results[0]["recommender_class_name"] == "IALS"


@pytest.mark.django_db
def test_unauthenticated_cannot_access_configurations(client: Client):
    """Unauthenticated users should get 401."""
    res = client.get(reverse("model_configuration-list"))
    assert res.status_code == 401
