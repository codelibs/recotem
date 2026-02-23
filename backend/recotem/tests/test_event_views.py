"""Tests for ConversionEventViewSet."""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse

from recotem.api.authentication import generate_api_key
from recotem.api.models import (
    ApiKey,
    ConversionEvent,
    DeploymentSlot,
    ModelConfiguration,
    Project,
    TrainedModel,
    TrainingData,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def _use_locmem_cache(settings):
    """Use in-memory cache so tests work without Redis."""
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }


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

    def test_create_impression_with_request_id(self, auth_client, project, slot):
        url = reverse("conversion_event-list")
        req_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        resp = auth_client.post(
            url,
            {
                "project": project.id,
                "deployment_slot": slot.id,
                "user_id": "user-1",
                "event_type": "impression",
                "recommendation_request_id": req_id,
            },
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["event_type"] == "impression"
        assert data["recommendation_request_id"] == req_id

    def test_unauthenticated_cannot_list(self, client: Client):
        url = reverse("conversion_event-list")
        resp = client.get(url)
        assert resp.status_code in (401, 403)


@pytest.fixture
def other_user(db):
    return User.objects.create_user(username="other_event_user", password="pass")


@pytest.fixture
def other_project(other_user):
    return Project.objects.create(
        name="other_project", user_column="u", item_column="i", owner=other_user
    )


@pytest.fixture
def api_key_data(user, project):
    full_key, prefix, hashed = generate_api_key()
    key_obj = ApiKey.objects.create(
        project=project,
        owner=user,
        name="event-key",
        key_prefix=prefix,
        hashed_key=hashed,
        scopes=["predict"],
    )
    return full_key, key_obj


@pytest.fixture
def other_slot(other_project):
    mc = ModelConfiguration.objects.create(
        name="other_cfg",
        project=other_project,
        recommender_class_name="IALSRecommender",
        parameters_json={},
    )
    td = TrainingData.objects.create(project=other_project)
    td.file.save("other_data.csv", ContentFile(b"u,i\n1,2\n"))
    tm = TrainedModel.objects.create(configuration=mc, data_loc=td)
    return DeploymentSlot.objects.create(
        project=other_project, name="other_slot", trained_model=tm, weight=100
    )


@pytest.mark.django_db
class TestConversionEventAccessControl:
    def test_api_key_wrong_project_rejected(
        self, client: Client, api_key_data, other_project, slot
    ):
        """API key for project A can't create event for project B."""
        full_key, key_obj = api_key_data
        url = reverse("conversion_event-list")
        resp = client.post(
            url,
            {
                "project": other_project.id,
                "deployment_slot": slot.id,
                "user_id": "user-1",
                "event_type": "impression",
            },
            content_type="application/json",
            HTTP_X_API_KEY=full_key,
        )
        assert resp.status_code == 403

    def test_jwt_non_owner_rejected(self, client: Client, other_user, project, slot):
        """JWT user who doesn't own project gets 403."""
        client.force_login(other_user)
        url = reverse("conversion_event-list")
        resp = client.post(
            url,
            {
                "project": project.id,
                "deployment_slot": slot.id,
                "user_id": "user-1",
                "event_type": "impression",
            },
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_slot_project_mismatch_rejected(self, auth_client, project, other_slot):
        """Slot from other_project used with project should be rejected."""
        url = reverse("conversion_event-list")
        resp = auth_client.post(
            url,
            {
                "project": project.id,
                "deployment_slot": other_slot.id,
                "user_id": "user-1",
                "event_type": "impression",
            },
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_api_key_predict_scope_allowed(
        self, client: Client, api_key_data, project, slot
    ):
        """API key with predict scope can create event for its own project."""
        full_key, key_obj = api_key_data
        url = reverse("conversion_event-list")
        resp = client.post(
            url,
            {
                "project": project.id,
                "deployment_slot": slot.id,
                "user_id": "user-1",
                "event_type": "impression",
            },
            content_type="application/json",
            HTTP_X_API_KEY=full_key,
        )
        assert resp.status_code == 201

    def test_batch_validates_each_event(
        self, client: Client, api_key_data, project, slot, other_project
    ):
        """Batch rejects if any event references the wrong project."""
        full_key, key_obj = api_key_data
        url = reverse("conversion_event-batch")
        resp = client.post(
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
                        "project": other_project.id,
                        "deployment_slot": slot.id,
                        "user_id": "u2",
                        "event_type": "click",
                    },
                ]
            },
            content_type="application/json",
            HTTP_X_API_KEY=full_key,
        )
        assert resp.status_code == 403
