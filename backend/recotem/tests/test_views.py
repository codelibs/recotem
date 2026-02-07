"""Tests for basic CRUD operations on ViewSets."""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from recotem.api.models import (
    Project,
    SplitConfig,
    EvaluationConfig,
    ModelConfiguration,
)


User = get_user_model()


@pytest.mark.django_db
class TestProjectViewSet:
    """Tests for ProjectViewSet CRUD operations."""

    def test_create_project(self, client: Client):
        """Test creating a new project."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        project_url = reverse("project-list")
        resp = client.post(
            project_url,
            dict(
                name="test_project",
                user_column="userId",
                item_column="movieId",
            ),
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test_project"
        assert data["user_column"] == "userId"
        assert data["item_column"] == "movieId"
        assert "id" in data

        # Verify the project exists in the database
        project = Project.objects.get(id=data["id"])
        assert project.name == "test_project"
        assert project.owner == user

    def test_create_project_with_time_column(self, client: Client):
        """Test creating a project with optional time_column."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        project_url = reverse("project-list")
        resp = client.post(
            project_url,
            dict(
                name="project_with_time",
                user_column="userId",
                item_column="movieId",
                time_column="timestamp",
            ),
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["time_column"] == "timestamp"

    def test_list_projects(self, client: Client):
        """Test listing projects."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        # Create some projects
        project_url = reverse("project-list")
        client.post(
            project_url,
            dict(name="project_1", user_column="userId", item_column="movieId"),
        )
        client.post(
            project_url,
            dict(name="project_2", user_column="userId", item_column="movieId"),
        )

        # List projects
        resp = client.get(project_url)
        assert resp.status_code == 200
        data = resp.json()

        # Check that both projects are in the list
        project_names = [p["name"] for p in data["results"]]
        assert "project_1" in project_names
        assert "project_2" in project_names

    def test_retrieve_project(self, client: Client):
        """Test retrieving a single project."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        # Create a project
        project_url = reverse("project-list")
        create_resp = client.post(
            project_url,
            dict(name="retrieve_test", user_column="userId", item_column="movieId"),
        )
        project_id = create_resp.json()["id"]

        # Retrieve the project
        detail_url = reverse("project-detail", args=[project_id])
        resp = client.get(detail_url)

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == project_id
        assert data["name"] == "retrieve_test"

    def test_update_project(self, client: Client):
        """Test updating a project."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        # Create a project
        project_url = reverse("project-list")
        create_resp = client.post(
            project_url,
            dict(name="original_name", user_column="userId", item_column="movieId"),
        )
        project_id = create_resp.json()["id"]

        # Update the project
        detail_url = reverse("project-detail", args=[project_id])
        resp = client.patch(
            detail_url,
            dict(name="updated_name"),
            content_type="application/json",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "updated_name"

        # Verify in database
        project = Project.objects.get(id=project_id)
        assert project.name == "updated_name"

    def test_delete_project(self, client: Client):
        """Test deleting a project."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        # Create a project
        project_url = reverse("project-list")
        create_resp = client.post(
            project_url,
            dict(name="to_delete", user_column="userId", item_column="movieId"),
        )
        project_id = create_resp.json()["id"]

        # Delete the project
        detail_url = reverse("project-detail", args=[project_id])
        resp = client.delete(detail_url)

        assert resp.status_code == 204

        # Verify it's deleted
        assert not Project.objects.filter(id=project_id).exists()


@pytest.mark.django_db
class TestSplitConfigViewSet:
    """Tests for SplitConfigViewSet CRUD operations."""

    def test_create_split_config(self, client: Client):
        """Test creating a new split configuration."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        split_url = reverse("split_config-list")
        resp = client.post(
            split_url,
            dict(
                name="test_split",
                scheme="RG",
                heldout_ratio=0.2,
                test_user_ratio=0.8,
                random_seed=42,
            ),
            content_type="application/json",
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test_split"
        assert data["scheme"] == "RG"
        assert data["heldout_ratio"] == 0.2
        assert "id" in data

        # Verify created_by is auto-set
        split = SplitConfig.objects.get(id=data["id"])
        assert split.created_by == user

    def test_create_split_config_auto_sets_created_by(self, client: Client):
        """Test that created_by is automatically set to the requesting user."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        split_url = reverse("split_config-list")
        resp = client.post(
            split_url,
            dict(name="auto_created_by", scheme="TG"),
            content_type="application/json",
        )

        assert resp.status_code == 201
        split_id = resp.json()["id"]

        # Verify created_by is set correctly
        split = SplitConfig.objects.get(id=split_id)
        assert split.created_by == user

    def test_list_split_configs(self, client: Client):
        """Test listing split configurations."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        split_url = reverse("split_config-list")

        # Create some split configs
        client.post(
            split_url,
            dict(name="split_1", scheme="RG"),
            content_type="application/json",
        )
        client.post(
            split_url,
            dict(name="split_2", scheme="TG"),
            content_type="application/json",
        )

        # List split configs
        resp = client.get(split_url)
        assert resp.status_code == 200
        data = resp.json()

        # Verify results
        split_names = [s["name"] for s in data["results"]]
        assert "split_1" in split_names
        assert "split_2" in split_names

    def test_create_split_config_with_defaults(self, client: Client):
        """Test creating a split config with default values."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        split_url = reverse("split_config-list")
        resp = client.post(
            split_url,
            dict(name="defaults_test"),
            content_type="application/json",
        )

        assert resp.status_code == 201
        data = resp.json()

        # Verify default values
        assert data["scheme"] == "RG"  # Default scheme
        assert data["heldout_ratio"] == 0.1
        assert data["test_user_ratio"] == 1.0
        assert data["random_seed"] == 42


@pytest.mark.django_db
class TestEvaluationConfigViewSet:
    """Tests for EvaluationConfigViewSet CRUD operations."""

    def test_create_evaluation_config(self, client: Client):
        """Test creating a new evaluation configuration."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        eval_url = reverse("evaluation_config-list")
        resp = client.post(
            eval_url,
            dict(
                name="test_eval",
                cutoff=10,
                target_metric="ndcg",
            ),
            content_type="application/json",
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test_eval"
        assert data["cutoff"] == 10
        assert data["target_metric"] == "ndcg"
        assert "id" in data

        # Verify created_by is auto-set
        eval_config = EvaluationConfig.objects.get(id=data["id"])
        assert eval_config.created_by == user

    def test_create_evaluation_config_auto_sets_created_by(self, client: Client):
        """Test that created_by is automatically set to the requesting user."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        eval_url = reverse("evaluation_config-list")
        resp = client.post(
            eval_url,
            dict(name="auto_created_by_eval"),
            content_type="application/json",
        )

        assert resp.status_code == 201
        eval_id = resp.json()["id"]

        # Verify created_by is set correctly
        eval_config = EvaluationConfig.objects.get(id=eval_id)
        assert eval_config.created_by == user

    def test_list_evaluation_configs(self, client: Client):
        """Test listing evaluation configurations."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        eval_url = reverse("evaluation_config-list")

        # Create some evaluation configs
        client.post(
            eval_url,
            dict(name="eval_1", cutoff=10),
            content_type="application/json",
        )
        client.post(
            eval_url,
            dict(name="eval_2", cutoff=20),
            content_type="application/json",
        )

        # List evaluation configs
        resp = client.get(eval_url)
        assert resp.status_code == 200
        data = resp.json()

        # Verify results
        eval_names = [e["name"] for e in data["results"]]
        assert "eval_1" in eval_names
        assert "eval_2" in eval_names

    def test_create_evaluation_config_with_defaults(self, client: Client):
        """Test creating an evaluation config with default values."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        eval_url = reverse("evaluation_config-list")
        resp = client.post(
            eval_url,
            dict(name="defaults_eval"),
            content_type="application/json",
        )

        assert resp.status_code == 201
        data = resp.json()

        # Verify default values
        assert data["cutoff"] == 20  # Default cutoff
        assert data["target_metric"] == "ndcg"  # Default metric

    def test_create_evaluation_config_with_different_metrics(self, client: Client):
        """Test creating evaluation configs with different target metrics."""
        user = User.objects.create_user(username="test_user", password="pass")
        client.force_login(user)

        eval_url = reverse("evaluation_config-list")

        # Test different metrics
        metrics = ["ndcg", "map", "recall", "hit"]
        for metric in metrics:
            resp = client.post(
                eval_url,
                dict(name=f"eval_{metric}", target_metric=metric),
                content_type="application/json",
            )

            assert resp.status_code == 201
            data = resp.json()
            assert data["target_metric"] == metric


@pytest.mark.django_db
class TestViewSetAuthentication:
    """Tests for authentication requirements on ViewSets."""

    def test_unauthenticated_cannot_create_split_config(self, client: Client):
        """Test that unauthenticated users cannot create split configs."""
        split_url = reverse("split_config-list")
        resp = client.post(
            split_url,
            dict(name="unauthorized_split"),
            content_type="application/json",
        )

        assert resp.status_code == 401

    def test_unauthenticated_cannot_create_evaluation_config(self, client: Client):
        """Test that unauthenticated users cannot create evaluation configs."""
        eval_url = reverse("evaluation_config-list")
        resp = client.post(
            eval_url,
            dict(name="unauthorized_eval"),
            content_type="application/json",
        )

        assert resp.status_code == 401

    def test_unauthenticated_cannot_create_project(self, client: Client):
        """Test that unauthenticated users cannot create projects."""
        project_url = reverse("project-list")
        resp = client.post(
            project_url,
            dict(name="unauthorized_project", user_column="u", item_column="i"),
        )

        assert resp.status_code == 401
