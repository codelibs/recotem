"""Tests for API key project scoping across management endpoints.

Verifies that an API key for project A cannot access resources in project B,
even when both projects are owned by the same user.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse

from recotem.api.authentication import generate_api_key
from recotem.api.models import (
    ABTest,
    ApiKey,
    DeploymentSlot,
    ModelConfiguration,
    Project,
    TrainedModel,
    TrainingData,
)

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="scope_user", password="pass")


@pytest.fixture
def project_a(user):
    return Project.objects.create(
        name="scope_proj_a", user_column="u", item_column="i", owner=user
    )


@pytest.fixture
def project_b(user):
    return Project.objects.create(
        name="scope_proj_b", user_column="u", item_column="i", owner=user
    )


def _make_api_key(project, user, scopes):
    """Create an API key and return (raw_key, key_obj)."""
    full_key, prefix, hashed = generate_api_key()
    key_obj = ApiKey.objects.create(
        project=project,
        owner=user,
        name=f"key-{project.name}",
        key_prefix=prefix,
        hashed_key=hashed,
        scopes=scopes,
    )
    return full_key, key_obj


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


@pytest.mark.django_db
class TestApiKeyProjectScopeOnViewSets:
    """API key for project A must not list or access project B resources."""

    def test_project_list_scoped(self, client: Client, user, project_a, project_b):
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])
        url = reverse("project-list")
        resp = client.get(url, HTTP_X_API_KEY=key_a)
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()["results"]]
        assert project_a.id in ids
        assert project_b.id not in ids

    def test_project_detail_scoped(self, client: Client, user, project_a, project_b):
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])
        # Can access own project
        resp = client.get(
            reverse("project-detail", args=[project_a.id]), HTTP_X_API_KEY=key_a
        )
        assert resp.status_code == 200
        # Cannot access other project
        resp = client.get(
            reverse("project-detail", args=[project_b.id]), HTTP_X_API_KEY=key_a
        )
        assert resp.status_code == 404

    def test_deployment_slot_list_scoped(
        self, client: Client, user, project_a, project_b
    ):
        _make_slot(project_a, "slot-a")
        _make_slot(project_b, "slot-b")
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])

        url = reverse("deployment_slot-list")
        resp = client.get(url, HTTP_X_API_KEY=key_a)
        assert resp.status_code == 200
        names = [s["name"] for s in resp.json()["results"]]
        assert "slot-a" in names
        assert "slot-b" not in names

    def test_ab_test_list_scoped(self, client: Client, user, project_a, project_b):
        slot_a1 = _make_slot(project_a, "ctrl-a")
        slot_a2 = _make_slot(project_a, "var-a")
        slot_b1 = _make_slot(project_b, "ctrl-b")
        slot_b2 = _make_slot(project_b, "var-b")
        ABTest.objects.create(
            project=project_a,
            name="test-a",
            control_slot=slot_a1,
            variant_slot=slot_a2,
        )
        ABTest.objects.create(
            project=project_b,
            name="test-b",
            control_slot=slot_b1,
            variant_slot=slot_b2,
        )
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])

        url = reverse("ab_test-list")
        resp = client.get(url, HTTP_X_API_KEY=key_a)
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["results"]]
        assert "test-a" in names
        assert "test-b" not in names

    def test_api_key_list_scoped(self, client: Client, user, project_a, project_b):
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])
        _make_api_key(project_b, user, ["read", "write"])

        url = reverse("api_key-list")
        resp = client.get(url, HTTP_X_API_KEY=key_a)
        assert resp.status_code == 200
        names = [k["name"] for k in resp.json()["results"]]
        assert f"key-{project_a.name}" in names
        assert f"key-{project_b.name}" not in names


@pytest.mark.django_db
class TestApiKeyProjectScopeOnCreate:
    """API key for project A must not create resources in project B."""

    def test_cannot_create_deployment_slot_in_other_project(
        self, client: Client, user, project_a, project_b
    ):
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])
        # Create a trained model in project B
        mc = ModelConfiguration.objects.create(
            name="cfg-b",
            project=project_b,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )
        td = TrainingData.objects.create(project=project_b)
        td.file.save("b.csv", ContentFile(b"u,i\n1,2\n"))
        tm = TrainedModel.objects.create(configuration=mc, data_loc=td)

        url = reverse("deployment_slot-list")
        resp = client.post(
            url,
            {
                "project": project_b.id,
                "name": "cross-proj-slot",
                "trained_model": tm.id,
                "weight": 50,
            },
            content_type="application/json",
            HTTP_X_API_KEY=key_a,
        )
        assert resp.status_code == 400
        assert "project" in resp.json()

    def test_cannot_create_ab_test_in_other_project(
        self, client: Client, user, project_a, project_b
    ):
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])
        slot_b1 = _make_slot(project_b, "ctrl-b")
        slot_b2 = _make_slot(project_b, "var-b")

        url = reverse("ab_test-list")
        resp = client.post(
            url,
            {
                "project": project_b.id,
                "name": "cross-proj-test",
                "control_slot": slot_b1.id,
                "variant_slot": slot_b2.id,
            },
            content_type="application/json",
            HTTP_X_API_KEY=key_a,
        )
        assert resp.status_code == 400
        assert "project" in resp.json()

    def test_cannot_create_api_key_for_other_project(
        self, client: Client, user, project_a, project_b
    ):
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])

        url = reverse("api_key-list")
        resp = client.post(
            url,
            {
                "project": project_b.id,
                "name": "cross-proj-key",
                "scopes": ["read"],
            },
            content_type="application/json",
            HTTP_X_API_KEY=key_a,
        )
        assert resp.status_code == 400
        assert "project" in resp.json()

    def test_can_create_in_own_project(
        self, client: Client, user, project_a, project_b
    ):
        """Verify that creating in the correct project still works."""
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])
        mc = ModelConfiguration.objects.create(
            name="cfg-a",
            project=project_a,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )
        td = TrainingData.objects.create(project=project_a)
        td.file.save("a.csv", ContentFile(b"u,i\n1,2\n"))
        tm = TrainedModel.objects.create(configuration=mc, data_loc=td)

        url = reverse("deployment_slot-list")
        resp = client.post(
            url,
            {
                "project": project_a.id,
                "name": "own-proj-slot",
                "trained_model": tm.id,
                "weight": 50,
            },
            content_type="application/json",
            HTTP_X_API_KEY=key_a,
        )
        assert resp.status_code == 201


@pytest.mark.django_db
class TestApiKeyTrainedModelScope:
    """API key for project A must not create trained_model with project B resources."""

    def test_cannot_create_trained_model_with_other_project_data(
        self, client: Client, user, project_a, project_b
    ):
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])
        # Resources in project B
        mc_b = ModelConfiguration.objects.create(
            name="cfg-b",
            project=project_b,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )
        td_b = TrainingData.objects.create(project=project_b)
        td_b.file.save("b.csv", ContentFile(b"u,i\n1,2\n"))

        url = reverse("trained_model-list")
        resp = client.post(
            url,
            {"data_loc": td_b.id, "configuration": mc_b.id},
            content_type="application/json",
            HTTP_X_API_KEY=key_a,
        )
        assert resp.status_code == 400
        assert "data_loc" in resp.json()

    def test_cannot_create_trained_model_with_other_project_config(
        self, client: Client, user, project_a, project_b
    ):
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])
        # data_loc in project A, configuration in project B
        td_a = TrainingData.objects.create(project=project_a)
        td_a.file.save("a.csv", ContentFile(b"u,i\n1,2\n"))
        mc_b = ModelConfiguration.objects.create(
            name="cfg-b",
            project=project_b,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )

        url = reverse("trained_model-list")
        resp = client.post(
            url,
            {"data_loc": td_a.id, "configuration": mc_b.id},
            content_type="application/json",
            HTTP_X_API_KEY=key_a,
        )
        assert resp.status_code == 400
        assert "configuration" in resp.json()


@pytest.mark.django_db
class TestPartialUpdateCrossProjectIntegrity:
    """PATCH must not allow cross-project data_loc/configuration mismatch."""

    def test_patch_configuration_to_different_project(
        self, client: Client, user, project_a, project_b
    ):
        """PATCH changing configuration to another project must be rejected."""
        client.force_login(user)
        # Trained model in project A
        mc_a = ModelConfiguration.objects.create(
            name="cfg-a",
            project=project_a,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )
        td_a = TrainingData.objects.create(project=project_a)
        td_a.file.save("a.csv", ContentFile(b"u,i\n1,2\n"))
        tm = TrainedModel.objects.create(configuration=mc_a, data_loc=td_a)

        # Configuration in project B
        mc_b = ModelConfiguration.objects.create(
            name="cfg-b",
            project=project_b,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )

        url = reverse("trained_model-detail", args=[tm.id])
        resp = client.patch(
            url,
            {"configuration": mc_b.id},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "configuration" in resp.json()

    def test_patch_data_loc_to_different_project(
        self, client: Client, user, project_a, project_b
    ):
        """PATCH changing data_loc to another project must be rejected."""
        client.force_login(user)
        mc_a = ModelConfiguration.objects.create(
            name="cfg-a",
            project=project_a,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )
        td_a = TrainingData.objects.create(project=project_a)
        td_a.file.save("a.csv", ContentFile(b"u,i\n1,2\n"))
        tm = TrainedModel.objects.create(configuration=mc_a, data_loc=td_a)

        td_b = TrainingData.objects.create(project=project_b)
        td_b.file.save("b.csv", ContentFile(b"u,i\n1,2\n"))

        url = reverse("trained_model-detail", args=[tm.id])
        resp = client.patch(
            url,
            {"data_loc": td_b.id},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "configuration" in resp.json()


@pytest.mark.django_db
class TestApiKeyCannotCreateProject:
    """API key must not be able to create new projects."""

    def test_cannot_create_project_with_api_key(
        self, client: Client, user, project_a, project_b
    ):
        key_a, _ = _make_api_key(project_a, user, ["read", "write"])
        url = reverse("project-list")
        resp = client.post(
            url,
            {
                "name": "new-project",
                "user_column": "u",
                "item_column": "i",
            },
            content_type="application/json",
            HTTP_X_API_KEY=key_a,
        )
        assert resp.status_code == 403

    def test_can_create_project_with_session_auth(
        self, client: Client, user, project_a, project_b
    ):
        """Session auth can still create projects."""
        client.force_login(user)
        url = reverse("project-list")
        resp = client.post(
            url,
            {
                "name": "new-project",
                "user_column": "u",
                "item_column": "i",
            },
            content_type="application/json",
        )
        assert resp.status_code == 201


@pytest.mark.django_db
class TestProjectSummaryApiKeyScope:
    """ProjectSummaryView must enforce API key project boundary."""

    def test_cannot_access_other_project_summary(
        self, client: Client, user, project_a, project_b
    ):
        key_a, _ = _make_api_key(project_a, user, ["read"])

        # Can access own project summary
        resp = client.get(
            f"/api/v1/project_summary/{project_a.id}/", HTTP_X_API_KEY=key_a
        )
        assert resp.status_code == 200

        # Cannot access other project summary
        resp = client.get(
            f"/api/v1/project_summary/{project_b.id}/", HTTP_X_API_KEY=key_a
        )
        assert resp.status_code == 404
