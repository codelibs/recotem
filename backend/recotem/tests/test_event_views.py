"""Tests for ConversionEventViewSet."""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse

from recotem.api.models import (
    ConversionEvent,
    DeploymentSlot,
    ModelConfiguration,
    Project,
    TrainedModel,
    TrainingData,
)

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="event_user", password="pass")


@pytest.fixture
def project(user):
    return Project.objects.create(
        name="event_project", user_column="u", item_column="i", owner=user
    )


@pytest.fixture
def slot(project):
    mc = ModelConfiguration.objects.create(
        name="cfg",
        project=project,
        recommender_class_name="IALSRecommender",
        parameters_json={},
    )
    td = TrainingData.objects.create(project=project)
    td.file.save("data.csv", ContentFile(b"u,i\n1,2\n"))
    tm = TrainedModel.objects.create(configuration=mc, data_loc=td)
    return DeploymentSlot.objects.create(
        project=project, name="slot", trained_model=tm, weight=100
    )


@pytest.fixture
def auth_client(client: Client, user):
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestConversionEventViewSet:
    def test_create_event(self, auth_client, project, slot):
        url = reverse("conversion_event-list")
        resp = auth_client.post(
            url,
            {
                "project": project.id,
                "deployment_slot": slot.id,
                "user_id": "user-1",
                "event_type": "impression",
            },
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["user_id"] == "user-1"
        assert data["event_type"] == "impression"

    def test_create_click_event(self, auth_client, project, slot):
        url = reverse("conversion_event-list")
        resp = auth_client.post(
            url,
            {
                "project": project.id,
                "deployment_slot": slot.id,
                "user_id": "user-2",
                "item_id": "item-10",
                "event_type": "click",
            },
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert resp.json()["event_type"] == "click"
        assert resp.json()["item_id"] == "item-10"

    def test_list_events(self, auth_client, project, slot):
        ConversionEvent.objects.create(
            project=project, deployment_slot=slot, user_id="u1", event_type="impression"
        )
        ConversionEvent.objects.create(
            project=project, deployment_slot=slot, user_id="u2", event_type="click"
        )
        url = reverse("conversion_event-list")
        resp = auth_client.get(url)
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 2

    def test_filter_by_event_type(self, auth_client, project, slot):
        ConversionEvent.objects.create(
            project=project, deployment_slot=slot, user_id="u1", event_type="impression"
        )
        ConversionEvent.objects.create(
            project=project, deployment_slot=slot, user_id="u2", event_type="click"
        )
        url = reverse("conversion_event-list")
        resp = auth_client.get(url, {"event_type": "click"})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        assert results[0]["event_type"] == "click"

    def test_batch_create_events(self, auth_client, project, slot):
        url = reverse("conversion_event-batch")
        resp = auth_client.post(
            url,
            {
                "events": [
                    {
                        "project": project.id,
                        "deployment_slot": slot.id,
                        "user_id": "u1",
                        "event_type": "impression",
                    },
                    {
                        "project": project.id,
                        "deployment_slot": slot.id,
                        "user_id": "u2",
                        "event_type": "click",
                    },
                ]
            },
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert resp.json()["created"] == 2
        assert ConversionEvent.objects.count() == 2

    def test_unauthenticated_cannot_list(self, client: Client):
        url = reverse("conversion_event-list")
        resp = client.get(url)
        assert resp.status_code in (401, 403)
