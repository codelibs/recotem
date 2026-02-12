"""Tests for DeploymentSlotViewSet CRUD operations."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from recotem.api.models import (
    DeploymentSlot,
    ModelConfiguration,
    Project,
    TrainedModel,
)

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="deploy_user", password="pass")


@pytest.fixture
def project(user):
    return Project.objects.create(
        name="deploy_project", user_column="u", item_column="i", owner=user
    )


@pytest.fixture
def trained_model(project, tmp_path):
    mc = ModelConfiguration.objects.create(
        name="cfg",
        project=project,
        recommender_class_name="IALSRecommender",
        parameters_json={},
    )
    # TrainedModel needs a data_loc — create a minimal one
    from django.core.files.base import ContentFile

    from recotem.api.models import TrainingData

    td = TrainingData.objects.create(project=project)
    td.file.save("data.csv", ContentFile(b"u,i\n1,2\n"))
    return TrainedModel.objects.create(
        configuration=mc,
        data_loc=td,
    )


@pytest.fixture
def auth_client(client: Client, user):
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestDeploymentSlotViewSet:
    def test_create_deployment_slot(self, auth_client, project, trained_model):
        url = reverse("deployment_slot-list")
        resp = auth_client.post(
            url,
            {
                "project": project.id,
                "name": "slot-a",
                "trained_model": trained_model.id,
                "weight": 50,
            },
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "slot-a"
        assert data["weight"] == 50
        assert data["is_active"] is True

    def test_list_deployment_slots(self, auth_client, project, trained_model):
        url = reverse("deployment_slot-list")
        auth_client.post(
            url,
            {
                "project": project.id,
                "name": "s1",
                "trained_model": trained_model.id,
                "weight": 60,
            },
            content_type="application/json",
        )
        auth_client.post(
            url,
            {
                "project": project.id,
                "name": "s2",
                "trained_model": trained_model.id,
                "weight": 40,
            },
            content_type="application/json",
        )
        resp = auth_client.get(url)
        assert resp.status_code == 200
        names = [s["name"] for s in resp.json()["results"]]
        assert "s1" in names
        assert "s2" in names

    def test_update_deployment_slot(self, auth_client, project, trained_model):
        url = reverse("deployment_slot-list")
        create_resp = auth_client.post(
            url,
            {
                "project": project.id,
                "name": "update-me",
                "trained_model": trained_model.id,
                "weight": 50,
            },
            content_type="application/json",
        )
        slot_id = create_resp.json()["id"]
        detail_url = reverse("deployment_slot-detail", args=[slot_id])
        resp = auth_client.patch(
            detail_url,
            {"weight": 80, "is_active": False},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["weight"] == 80
        assert resp.json()["is_active"] is False

    def test_delete_deployment_slot(self, auth_client, project, trained_model):
        url = reverse("deployment_slot-list")
        create_resp = auth_client.post(
            url,
            {
                "project": project.id,
                "name": "del-me",
                "trained_model": trained_model.id,
                "weight": 50,
            },
            content_type="application/json",
        )
        slot_id = create_resp.json()["id"]
        detail_url = reverse("deployment_slot-detail", args=[slot_id])
        resp = auth_client.delete(detail_url)
        assert resp.status_code == 204
        assert not DeploymentSlot.objects.filter(id=slot_id).exists()

    def test_filter_by_project(self, auth_client, user, trained_model):
        p2 = Project.objects.create(
            name="p2", user_column="u", item_column="i", owner=user
        )
        mc2 = ModelConfiguration.objects.create(
            name="cfg2",
            project=p2,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )
        from django.core.files.base import ContentFile

        from recotem.api.models import TrainingData

        td2 = TrainingData.objects.create(project=p2)
        td2.file.save("d2.csv", ContentFile(b"u,i\n1,2\n"))
        tm2 = TrainedModel.objects.create(configuration=mc2, data_loc=td2)

        url = reverse("deployment_slot-list")
        auth_client.post(
            url,
            {
                "project": trained_model.configuration.project.id,
                "name": "s-orig",
                "trained_model": trained_model.id,
                "weight": 50,
            },
            content_type="application/json",
        )
        auth_client.post(
            url,
            {"project": p2.id, "name": "s-p2", "trained_model": tm2.id, "weight": 50},
            content_type="application/json",
        )
        resp = auth_client.get(url, {"project": p2.id})
        names = [s["name"] for s in resp.json()["results"]]
        assert "s-p2" in names
        assert "s-orig" not in names

    def test_unauthenticated_cannot_list(self, client: Client):
        url = reverse("deployment_slot-list")
        resp = client.get(url)
        assert resp.status_code == 401

    def test_cross_project_trained_model_rejected(self, auth_client, user, project):
        """Cannot attach a trained_model from another project to a slot."""
        other_project = Project.objects.create(
            name="other", user_column="u", item_column="i", owner=user
        )
        mc = ModelConfiguration.objects.create(
            name="cfg-other",
            project=other_project,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )
        from django.core.files.base import ContentFile

        from recotem.api.models import TrainingData

        td = TrainingData.objects.create(project=other_project)
        td.file.save("other.csv", ContentFile(b"u,i\n1,2\n"))
        other_model = TrainedModel.objects.create(configuration=mc, data_loc=td)

        url = reverse("deployment_slot-list")
        resp = auth_client.post(
            url,
            {
                "project": project.id,
                "name": "cross-proj-slot",
                "trained_model": other_model.id,
                "weight": 50,
            },
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "trained_model" in resp.json()
