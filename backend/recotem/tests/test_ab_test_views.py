"""Tests for ABTestViewSet CRUD and custom actions."""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse

from recotem.api.models import (
    ABTest,
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
    return User.objects.create_user(username="ab_user", password="pass")


@pytest.fixture
def project(user):
    return Project.objects.create(
        name="ab_project", user_column="u", item_column="i", owner=user
    )


def _make_slot(project, name, weight=50):
    mc = ModelConfiguration.objects.create(
        name=f"cfg-{name}",
        project=project,
        recommender_class_name="IALSRecommender",
        parameters_json={},
    )
    td = TrainingData.objects.create(project=project)
    td.file.save(f"{name}.csv", ContentFile(b"u,i\n1,2\n"))
    tm = TrainedModel.objects.create(configuration=mc, data_loc=td)
    return DeploymentSlot.objects.create(
        project=project, name=name, trained_model=tm, weight=weight
    )


@pytest.fixture
def control_slot(project):
    return _make_slot(project, "control")


@pytest.fixture
def variant_slot(project):
    return _make_slot(project, "variant")


@pytest.fixture
def auth_client(client: Client, user):
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestABTestViewSet:
    def test_create_ab_test(self, auth_client, project, control_slot, variant_slot):
        url = reverse("ab_test-list")
        resp = auth_client.post(
            url,
            {
                "project": project.id,
                "name": "test-1",
                "control_slot": control_slot.id,
                "variant_slot": variant_slot.id,
            },
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test-1"
        assert data["status"] == "DRAFT"

    def test_list_ab_tests(self, auth_client, project, control_slot, variant_slot):
        ABTest.objects.create(
            project=project, name="t1", control_slot=control_slot, variant_slot=variant_slot
        )
        ABTest.objects.create(
            project=project, name="t2", control_slot=control_slot, variant_slot=variant_slot
        )
        url = reverse("ab_test-list")
        resp = auth_client.get(url)
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["results"]]
        assert "t1" in names
        assert "t2" in names

    def test_start_ab_test(self, auth_client, project, control_slot, variant_slot):
        test = ABTest.objects.create(
            project=project, name="start-me", control_slot=control_slot, variant_slot=variant_slot
        )
        url = reverse("ab_test-start", args=[test.id])
        resp = auth_client.post(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "RUNNING"
        assert data["started_at"] is not None

    def test_start_non_draft_fails(self, auth_client, project, control_slot, variant_slot):
        test = ABTest.objects.create(
            project=project,
            name="running",
            control_slot=control_slot,
            variant_slot=variant_slot,
            status=ABTest.Status.RUNNING,
        )
        url = reverse("ab_test-start", args=[test.id])
        resp = auth_client.post(url)
        assert resp.status_code == 400

    def test_stop_ab_test(self, auth_client, project, control_slot, variant_slot):
        test = ABTest.objects.create(
            project=project,
            name="stop-me",
            control_slot=control_slot,
            variant_slot=variant_slot,
            status=ABTest.Status.RUNNING,
        )
        url = reverse("ab_test-stop", args=[test.id])
        resp = auth_client.post(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["ended_at"] is not None

    def test_stop_non_running_fails(self, auth_client, project, control_slot, variant_slot):
        test = ABTest.objects.create(
            project=project,
            name="draft",
            control_slot=control_slot,
            variant_slot=variant_slot,
            status=ABTest.Status.DRAFT,
        )
        url = reverse("ab_test-stop", args=[test.id])
        resp = auth_client.post(url)
        assert resp.status_code == 400

    def test_results_no_events(self, auth_client, project, control_slot, variant_slot):
        test = ABTest.objects.create(
            project=project, name="no-events", control_slot=control_slot, variant_slot=variant_slot
        )
        url = reverse("ab_test-results", args=[test.id])
        resp = auth_client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data["control_impressions"] == 0
        assert data["variant_impressions"] == 0
        assert data["significant"] is False

    def test_results_with_events(self, auth_client, project, control_slot, variant_slot):
        test = ABTest.objects.create(
            project=project,
            name="with-events",
            control_slot=control_slot,
            variant_slot=variant_slot,
            status=ABTest.Status.RUNNING,
        )
        # Create impression and click events
        for i in range(100):
            ConversionEvent.objects.create(
                project=project,
                deployment_slot=control_slot,
                user_id=f"u{i}",
                event_type="impression",
            )
        for i in range(10):
            ConversionEvent.objects.create(
                project=project,
                deployment_slot=control_slot,
                user_id=f"u{i}",
                event_type="click",
            )
        for i in range(100):
            ConversionEvent.objects.create(
                project=project,
                deployment_slot=variant_slot,
                user_id=f"v{i}",
                event_type="impression",
            )
        for i in range(20):
            ConversionEvent.objects.create(
                project=project,
                deployment_slot=variant_slot,
                user_id=f"v{i}",
                event_type="click",
            )

        url = reverse("ab_test-results", args=[test.id])
        resp = auth_client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data["control_impressions"] == 100
        assert data["control_conversions"] == 10
        assert data["variant_impressions"] == 100
        assert data["variant_conversions"] == 20
        assert data["control_rate"] == pytest.approx(0.1, abs=1e-6)
        assert data["variant_rate"] == pytest.approx(0.2, abs=1e-6)

    def test_promote_winner(self, auth_client, project, control_slot, variant_slot):
        test = ABTest.objects.create(
            project=project,
            name="promote",
            control_slot=control_slot,
            variant_slot=variant_slot,
            status=ABTest.Status.COMPLETED,
        )
        url = reverse("ab_test-promote_winner", args=[test.id])
        resp = auth_client.post(
            url, {"slot_id": control_slot.id}, content_type="application/json"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "promoted"
        assert resp.json()["winner_slot_id"] == control_slot.id

        # Verify winner has weight 100, loser is deactivated
        control_slot.refresh_from_db()
        variant_slot.refresh_from_db()
        assert control_slot.weight == 100
        assert variant_slot.is_active is False

    def test_promote_winner_non_completed_fails(
        self, auth_client, project, control_slot, variant_slot
    ):
        test = ABTest.objects.create(
            project=project,
            name="not-done",
            control_slot=control_slot,
            variant_slot=variant_slot,
            status=ABTest.Status.RUNNING,
        )
        url = reverse("ab_test-promote_winner", args=[test.id])
        resp = auth_client.post(
            url, {"slot_id": control_slot.id}, content_type="application/json"
        )
        assert resp.status_code == 400

    def test_promote_winner_invalid_slot_fails(
        self, auth_client, project, control_slot, variant_slot
    ):
        test = ABTest.objects.create(
            project=project,
            name="bad-slot",
            control_slot=control_slot,
            variant_slot=variant_slot,
            status=ABTest.Status.COMPLETED,
        )
        url = reverse("ab_test-promote_winner", args=[test.id])
        resp = auth_client.post(
            url, {"slot_id": 99999}, content_type="application/json"
        )
        assert resp.status_code == 400

    def test_unauthenticated_cannot_list(self, client: Client):
        url = reverse("ab_test-list")
        resp = client.get(url)
        assert resp.status_code == 401


@pytest.mark.django_db
class TestABTestOwnershipIsolation:
    def test_user_cannot_see_other_users_tests(self, client: Client):
        user_a = User.objects.create_user(username="a", password="pass")
        user_b = User.objects.create_user(username="b", password="pass")
        proj_a = Project.objects.create(name="pa", user_column="u", item_column="i", owner=user_a)
        c = _make_slot(proj_a, "c-a")
        v = _make_slot(proj_a, "v-a")
        ABTest.objects.create(project=proj_a, name="secret-test", control_slot=c, variant_slot=v)

        url = reverse("ab_test-list")
        client.force_login(user_b)
        resp = client.get(url)
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["results"]]
        assert "secret-test" not in names
