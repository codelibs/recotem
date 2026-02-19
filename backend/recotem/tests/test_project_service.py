"""Unit tests for project_service.py — project lookup and summary aggregation."""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile

from recotem.api.exceptions import ResourceNotFoundError
from recotem.api.models import (
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TrainedModel,
    TrainingData,
)
from recotem.api.services.project_service import get_project_or_404, get_project_summary

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
    return User.objects.create_user(username="owner", password="OwnerPass123!")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(username="other", password="OtherPass123!")


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="staff", password="StaffPass123!", is_staff=True
    )


@pytest.fixture
def project(user):
    return Project.objects.create(
        name="TestProject",
        owner=user,
        user_column="user_id",
        item_column="item_id",
    )


@pytest.fixture
def unowned_project(db):
    return Project.objects.create(
        name="LegacyProject",
        owner=None,
        user_column="user_id",
        item_column="item_id",
    )


@pytest.mark.django_db
class TestGetProjectOr404:
    def test_existing_project_returned(self, project, user):
        """Returns project when user is owner."""
        result = get_project_or_404(project.pk, user=user)
        assert result.pk == project.pk
        assert result.name == "TestProject"

    def test_nonexistent_project_raises(self, user):
        """Raises ResourceNotFoundError for missing pk."""
        with pytest.raises(ResourceNotFoundError, match="not found"):
            get_project_or_404(999999, user=user)

    def test_staff_bypasses_ownership(self, project, staff_user):
        """Staff user can access any project regardless of ownership."""
        result = get_project_or_404(project.pk, user=staff_user)
        assert result.pk == project.pk

    def test_non_owner_denied(self, project, other_user):
        """Non-owner non-staff gets ResourceNotFoundError."""
        with pytest.raises(ResourceNotFoundError, match="not found"):
            get_project_or_404(project.pk, user=other_user)

    def test_unowned_project_visible(self, unowned_project, other_user):
        """Project with owner=None accessible by any authenticated user."""
        result = get_project_or_404(unowned_project.pk, user=other_user)
        assert result.pk == unowned_project.pk

    def test_no_user_returns_project(self, project):
        """user=None skips ownership check entirely."""
        result = get_project_or_404(project.pk, user=None)
        assert result.pk == project.pk


@pytest.mark.django_db
class TestGetProjectSummary:
    def test_counts_correct(self, project, user):
        """Correct n_data, n_complete_jobs, n_models for populated project."""
        # Create TrainingData with a file and set filesize manually
        # (post_save signal only fires on create, but file is saved after)
        td = TrainingData.objects.create(project=project)
        td.file.save("data.csv", ContentFile(b"user_id,item_id\n1,2\n"))
        td.filesize = td.file.size
        td.save(update_fields=["filesize"])

        # Create ModelConfiguration linked to project
        config = ModelConfiguration.objects.create(
            name="cfg",
            project=project,
            recommender_class_name="TopPopRecommender",
            parameters_json={},
        )

        # Create a ParameterTuningJob with best_config pointing to config
        sc = SplitConfig.objects.create(created_by=user)
        ec = EvaluationConfig.objects.create(created_by=user)
        ParameterTuningJob.objects.create(
            data=td, split=sc, evaluation=ec, best_config=config
        )

        # Create TrainedModel with a file and set filesize manually
        tm = TrainedModel.objects.create(configuration=config, data_loc=td)
        tm.file.save("model.pkl", ContentFile(b"fake model data"))
        tm.filesize = tm.file.size
        tm.save(update_fields=["filesize"])

        summary = get_project_summary(project)
        assert summary["n_data"] == 1
        assert summary["n_complete_jobs"] == 1
        assert summary["n_models"] == 1
        assert summary["ins_datetime"] == project.ins_datetime

    def test_empty_project_zeros(self, project):
        """All zeros for project with no data, configs, or models."""
        summary = get_project_summary(project)
        assert summary["n_data"] == 0
        assert summary["n_complete_jobs"] == 0
        assert summary["n_models"] == 0
        assert summary["ins_datetime"] == project.ins_datetime
