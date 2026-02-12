"""Tests for RetrainingScheduleViewSet and RetrainingRunViewSet."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from unittest.mock import patch

from recotem.api.models import Project, RetrainingRun, RetrainingSchedule

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="retrain_user", password="pass")


@pytest.fixture
def project(user):
    return Project.objects.create(
        name="retrain_project", user_column="u", item_column="i", owner=user
    )


@pytest.fixture
def auth_client(client: Client, user):
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestRetrainingScheduleViewSet:
    @patch("recotem.api.views.retraining.sync_schedule_to_beat")
    def test_create_schedule(self, mock_sync, auth_client, project):
        url = reverse("retraining_schedule-list")
        resp = auth_client.post(
            url,
            {
                "project": project.id,
                "is_enabled": False,
                "cron_expression": "0 3 * * 1",
            },
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["project"] == project.id
        assert data["cron_expression"] == "0 3 * * 1"
        mock_sync.assert_called_once()

    @patch("recotem.api.views.retraining.sync_schedule_to_beat")
    def test_list_schedules(self, mock_sync, auth_client, project):
        RetrainingSchedule.objects.create(project=project, cron_expression="0 2 * * 0")
        url = reverse("retraining_schedule-list")
        resp = auth_client.get(url)
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1

    @patch("recotem.api.views.retraining.sync_schedule_to_beat")
    def test_update_schedule(self, mock_sync, auth_client, project):
        sched = RetrainingSchedule.objects.create(
            project=project, cron_expression="0 2 * * 0"
        )
        url = reverse("retraining_schedule-detail", args=[sched.id])
        resp = auth_client.patch(
            url, {"is_enabled": True}, content_type="application/json"
        )
        assert resp.status_code == 200
        assert resp.json()["is_enabled"] is True
        mock_sync.assert_called_once()

    @patch("recotem.api.views.retraining.sync_schedule_to_beat")
    def test_delete_schedule(self, mock_sync, auth_client, project):
        sched = RetrainingSchedule.objects.create(
            project=project, cron_expression="0 2 * * 0"
        )
        url = reverse("retraining_schedule-detail", args=[sched.id])
        resp = auth_client.delete(url)
        assert resp.status_code == 204
        assert not RetrainingSchedule.objects.filter(id=sched.id).exists()

    def test_invalid_cron_expression(self, auth_client, project):
        url = reverse("retraining_schedule-list")
        resp = auth_client.post(
            url,
            {"project": project.id, "cron_expression": "bad cron"},
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("recotem.api.views.retraining.sync_schedule_to_beat")
    @patch("recotem.api.tasks.task_scheduled_retrain.delay")
    def test_trigger_retraining(self, mock_task, mock_sync, auth_client, project):
        sched = RetrainingSchedule.objects.create(
            project=project, cron_expression="0 2 * * 0", is_enabled=True
        )
        url = reverse("retraining_schedule-trigger", args=[sched.id])
        resp = auth_client.post(url)
        assert resp.status_code == 202
        assert resp.json()["status"] == "triggered"
        mock_task.assert_called_once_with(sched.id)

    def test_unauthenticated_cannot_list(self, client: Client):
        url = reverse("retraining_schedule-list")
        resp = client.get(url)
        assert resp.status_code == 401


@pytest.mark.django_db
class TestRetrainingRunViewSet:
    def test_list_runs(self, auth_client, project):
        sched = RetrainingSchedule.objects.create(
            project=project, cron_expression="0 2 * * 0"
        )
        RetrainingRun.objects.create(schedule=sched, status="COMPLETED")
        RetrainingRun.objects.create(schedule=sched, status="FAILED", error_message="oops")

        url = reverse("retraining_run-list")
        resp = auth_client.get(url, {"schedule": sched.id})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 2
        statuses = {r["status"] for r in results}
        assert "COMPLETED" in statuses
        assert "FAILED" in statuses

    def test_retrieve_run(self, auth_client, project):
        sched = RetrainingSchedule.objects.create(
            project=project, cron_expression="0 2 * * 0"
        )
        run = RetrainingRun.objects.create(
            schedule=sched, status="COMPLETED", error_message=""
        )
        url = reverse("retraining_run-detail", args=[run.id])
        resp = auth_client.get(url)
        assert resp.status_code == 200
        assert resp.json()["status"] == "COMPLETED"

    def test_unauthenticated_cannot_list_runs(self, client: Client):
        url = reverse("retraining_run-list")
        resp = client.get(url)
        assert resp.status_code == 401


@pytest.mark.django_db
class TestRetrainingOwnershipIsolation:
    def test_user_cannot_see_other_users_schedules(self, client: Client):
        user_a = User.objects.create_user(username="a", password="pass")
        user_b = User.objects.create_user(username="b", password="pass")
        proj_a = Project.objects.create(name="pa", user_column="u", item_column="i", owner=user_a)
        RetrainingSchedule.objects.create(project=proj_a, cron_expression="0 2 * * 0")

        url = reverse("retraining_schedule-list")
        client.force_login(user_b)
        resp = client.get(url)
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 0
