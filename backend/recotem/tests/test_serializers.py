"""Unit tests for serializer validation logic."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory

from recotem.api.models import (
    EvaluationConfig,
    Project,
    SplitConfig,
)
from recotem.api.serializers.project import ProjectSerializer

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", password="testpass123")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(username="otheruser", password="testpass456")


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.mark.django_db
class TestProjectSerializer:
    def test_valid_project(self, user, factory):
        request = factory.post("/api/v1/project/")
        request.user = user
        data = {
            "name": "My Project",
            "user_column": "user_id",
            "item_column": "item_id",
        }
        serializer = ProjectSerializer(data=data, context={"request": request})
        assert serializer.is_valid(), serializer.errors
        project = serializer.save(owner=user)
        assert project.name == "My Project"
        assert project.owner == user

    def test_duplicate_name_same_owner(self, user, factory):
        Project.objects.create(name="Dup", user_column="u", item_column="i", owner=user)
        request = factory.post("/api/v1/project/")
        request.user = user
        data = {"name": "Dup", "user_column": "u", "item_column": "i"}
        serializer = ProjectSerializer(data=data, context={"request": request})
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_same_name_different_owner(self, user, other_user, factory):
        Project.objects.create(
            name="Shared", user_column="u", item_column="i", owner=user
        )
        request = factory.post("/api/v1/project/")
        request.user = other_user
        data = {"name": "Shared", "user_column": "u", "item_column": "i"}
        serializer = ProjectSerializer(data=data, context={"request": request})
        assert serializer.is_valid(), serializer.errors

    def test_owner_is_read_only(self, user, factory):
        request = factory.post("/api/v1/project/")
        request.user = user
        data = {
            "name": "Test",
            "user_column": "u",
            "item_column": "i",
            "owner": 999,
        }
        serializer = ProjectSerializer(data=data, context={"request": request})
        assert serializer.is_valid(), serializer.errors
        # owner should not be set from input data
        assert "owner" not in serializer.validated_data


@pytest.mark.django_db
class TestModelCRUD:
    """Basic CRUD operations on core models."""

    def test_project_create_and_str(self, user):
        p = Project.objects.create(
            name="Test", user_column="u", item_column="i", owner=user
        )
        assert p.pk is not None
        assert Project.objects.filter(pk=p.pk).exists()

    def test_split_config_defaults(self, user):
        sc = SplitConfig.objects.create(name="default", created_by=user)
        assert sc.scheme == SplitConfig.SplitScheme.RANDOM
        assert sc.heldout_ratio == 0.1
        assert sc.random_seed == 42

    def test_evaluation_config_defaults(self, user):
        ec = EvaluationConfig.objects.create(name="default", created_by=user)
        assert ec.cutoff == 20
        assert ec.target_metric == EvaluationConfig.TargetMetric.NDCG

    def test_project_delete_cascades(self, user):
        p = Project.objects.create(
            name="ToDelete", user_column="u", item_column="i", owner=user
        )
        pid = p.pk
        p.delete()
        assert not Project.objects.filter(pk=pid).exists()

    def test_project_update(self, user):
        p = Project.objects.create(
            name="Original", user_column="u", item_column="i", owner=user
        )
        p.name = "Updated"
        p.save()
        p.refresh_from_db()
        assert p.name == "Updated"


@pytest.mark.django_db
class TestModelConfigurationSerializer:
    """Tests for ModelConfigurationSerializer validation."""

    def test_valid_recommender_class_name(self, user, factory):
        """Valid Python identifiers should pass validation."""
        from recotem.api.serializers import ModelConfigurationSerializer

        p = Project.objects.create(
            name="ModelTest", user_column="u", item_column="i", owner=user
        )
        request = factory.post("/api/v1/model_configuration/")
        request.user = user

        valid_names = [
            "IALSRecommender",
            "TopPopRecommender",
            "CosineKNNRecommender",
        ]

        for name in valid_names:
            data = {
                "name": f"config_{name}",
                "project": p.id,
                "recommender_class_name": name,
                "parameters_json": {},
            }
            serializer = ModelConfigurationSerializer(
                data=data, context={"request": request}
            )
            assert serializer.is_valid(), f"{name} should be valid: {serializer.errors}"

    def test_invalid_recommender_class_name_rejects_path_traversal(self, user, factory):
        """Path traversal attempts should be rejected."""
        from recotem.api.serializers import ModelConfigurationSerializer

        p = Project.objects.create(
            name="SecurityTest", user_column="u", item_column="i", owner=user
        )
        request = factory.post("/api/v1/model_configuration/")
        request.user = user

        invalid_names = [
            "../../evil",
            "../EvilRecommender",
            "path/to/Evil",
            "Evil.Recommender",
            "Evil-Recommender",
            "123StartsWithNumber",
            "",
            "Has Space",
            "Has-Dash",
            "Has.Dot",
            "Evil@Recommender",
        ]

        for name in invalid_names:
            data = {
                "name": f"config_{name}",
                "project": p.id,
                "recommender_class_name": name,
                "parameters_json": {},
            }
            serializer = ModelConfigurationSerializer(
                data=data, context={"request": request}
            )
            assert not serializer.is_valid(), f"{name} should be invalid"
            assert "recommender_class_name" in serializer.errors

    def test_recommender_class_name_must_be_identifier(self, user, factory):
        """Only valid Python identifiers should pass."""
        from recotem.api.serializers import ModelConfigurationSerializer

        p = Project.objects.create(
            name="IdentifierTest", user_column="u", item_column="i", owner=user
        )
        request = factory.post("/api/v1/model_configuration/")
        request.user = user

        data = {
            "name": "special_chars",
            "project": p.id,
            "recommender_class_name": "Evil$Recommender",
            "parameters_json": {},
        }
        serializer = ModelConfigurationSerializer(
            data=data, context={"request": request}
        )
        assert not serializer.is_valid()
        assert "recommender_class_name" in serializer.errors

    def test_parameters_json_valid(self, user, factory):
        """Valid JSON object should pass validation."""
        from recotem.api.serializers import ModelConfigurationSerializer

        p = Project.objects.create(
            name="JsonValid", user_column="u", item_column="i", owner=user
        )
        request = factory.post("/api/v1/model_configuration/")
        request.user = user
        data = {
            "name": "json_test",
            "project": p.id,
            "recommender_class_name": "IALSRecommender",
            "parameters_json": {"alpha": 0.1, "n_components": 64},
        }
        serializer = ModelConfigurationSerializer(
            data=data, context={"request": request}
        )
        assert serializer.is_valid(), serializer.errors

    def test_parameters_json_non_dict_rejected(self, user, factory):
        """Non-dict type (string) should be rejected."""
        from recotem.api.serializers import ModelConfigurationSerializer

        p = Project.objects.create(
            name="JsonInvalid", user_column="u", item_column="i", owner=user
        )
        request = factory.post("/api/v1/model_configuration/")
        request.user = user
        data = {
            "name": "bad_json",
            "project": p.id,
            "recommender_class_name": "IALSRecommender",
            "parameters_json": "not a dict",
        }
        serializer = ModelConfigurationSerializer(
            data=data, context={"request": request}
        )
        assert not serializer.is_valid()
        assert "parameters_json" in serializer.errors

    def test_parameters_json_array_rejected(self, user, factory):
        """JSON array (not object) should be rejected."""
        from recotem.api.serializers import ModelConfigurationSerializer

        p = Project.objects.create(
            name="JsonArray", user_column="u", item_column="i", owner=user
        )
        request = factory.post("/api/v1/model_configuration/")
        request.user = user
        data = {
            "name": "array_json",
            "project": p.id,
            "recommender_class_name": "IALSRecommender",
            "parameters_json": [1, 2, 3],
        }
        serializer = ModelConfigurationSerializer(
            data=data, context={"request": request}
        )
        assert not serializer.is_valid()
        assert "parameters_json" in serializer.errors


@pytest.mark.django_db
class TestParameterTuningJobSerializer:
    """Tests for ParameterTuningJobSerializer validation."""

    def test_status_is_read_only(self, user, factory):
        """Status field should not be settable via the serializer."""
        from recotem.api.serializers.tuning_job import ParameterTuningJobSerializer

        serializer = ParameterTuningJobSerializer()
        assert "status" in serializer.Meta.read_only_fields

    def test_cross_owner_data_rejected(self, user, other_user, factory):
        """Using another user's training data should be rejected."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from recotem.api.models import TrainingData
        from recotem.api.serializers.tuning_job import ParameterTuningJobSerializer

        p = Project.objects.create(
            name="OtherOwner", user_column="u", item_column="i", owner=other_user
        )
        td = TrainingData.objects.create(
            project=p,
            file=SimpleUploadedFile("data.csv", b"a,b\n1,2\n", content_type="text/csv"),
        )
        sc = SplitConfig.objects.create(name="s", created_by=user)
        ec = EvaluationConfig.objects.create(name="e", created_by=user)

        request = factory.post("/api/v1/parameter_tuning_job/")
        request.user = user
        data = {"data": td.id, "split": sc.id, "evaluation": ec.id, "n_trials": 2}
        serializer = ParameterTuningJobSerializer(
            data=data, context={"request": request}
        )
        assert not serializer.is_valid()
        assert "data" in serializer.errors

    def test_cross_owner_split_rejected(self, user, other_user, factory):
        """Using another user's split config should be rejected."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from recotem.api.models import TrainingData
        from recotem.api.serializers.tuning_job import ParameterTuningJobSerializer

        p = Project.objects.create(
            name="MySplit", user_column="u", item_column="i", owner=user
        )
        td = TrainingData.objects.create(
            project=p,
            file=SimpleUploadedFile("data.csv", b"a,b\n1,2\n", content_type="text/csv"),
        )
        sc = SplitConfig.objects.create(name="s", created_by=other_user)
        ec = EvaluationConfig.objects.create(name="e", created_by=user)

        request = factory.post("/api/v1/parameter_tuning_job/")
        request.user = user
        data = {"data": td.id, "split": sc.id, "evaluation": ec.id, "n_trials": 2}
        serializer = ParameterTuningJobSerializer(
            data=data, context={"request": request}
        )
        assert not serializer.is_valid()
        assert "split" in serializer.errors

    def test_cross_owner_evaluation_rejected(self, user, other_user, factory):
        """Using another user's evaluation config should be rejected."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from recotem.api.models import TrainingData
        from recotem.api.serializers.tuning_job import ParameterTuningJobSerializer

        p = Project.objects.create(
            name="MyEval", user_column="u", item_column="i", owner=user
        )
        td = TrainingData.objects.create(
            project=p,
            file=SimpleUploadedFile("data.csv", b"a,b\n1,2\n", content_type="text/csv"),
        )
        sc = SplitConfig.objects.create(name="s", created_by=user)
        ec = EvaluationConfig.objects.create(name="e", created_by=other_user)

        request = factory.post("/api/v1/parameter_tuning_job/")
        request.user = user
        data = {"data": td.id, "split": sc.id, "evaluation": ec.id, "n_trials": 2}
        serializer = ParameterTuningJobSerializer(
            data=data, context={"request": request}
        )
        assert not serializer.is_valid()
        assert "evaluation" in serializer.errors

    def test_missing_required_fields(self, user, factory):
        """Missing required fields should produce validation errors."""
        from recotem.api.serializers.tuning_job import ParameterTuningJobSerializer

        request = factory.post("/api/v1/parameter_tuning_job/")
        request.user = user
        serializer = ParameterTuningJobSerializer(data={}, context={"request": request})
        assert not serializer.is_valid()
        assert "data" in serializer.errors

    def test_tried_algorithms_json_valid(self, user, factory):
        """Valid JSON array of strings should pass validation."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from recotem.api.models import TrainingData
        from recotem.api.serializers.tuning_job import ParameterTuningJobSerializer

        p = Project.objects.create(
            name="AlgoJson", user_column="u", item_column="i", owner=user
        )
        td = TrainingData.objects.create(
            project=p,
            file=SimpleUploadedFile("data.csv", b"u,i\n1,2\n", content_type="text/csv"),
        )
        sc = SplitConfig.objects.create(name="s", created_by=user)
        ec = EvaluationConfig.objects.create(name="e", created_by=user)

        request = factory.post("/api/v1/parameter_tuning_job/")
        request.user = user
        data = {
            "data": td.id,
            "split": sc.id,
            "evaluation": ec.id,
            "n_trials": 2,
            "tried_algorithms_json": ["IALSRecommender", "TopPopRecommender"],
        }
        serializer = ParameterTuningJobSerializer(
            data=data, context={"request": request}
        )
        assert serializer.is_valid(), serializer.errors

    def test_tried_algorithms_json_invalid_json(self, user, factory):
        """Invalid JSON should be rejected."""
        from recotem.api.serializers.tuning_job import ParameterTuningJobSerializer

        request = factory.post("/api/v1/parameter_tuning_job/")
        request.user = user
        data = {
            "data": 1,
            "split": 1,
            "evaluation": 1,
            "tried_algorithms_json": "not valid json",
        }
        serializer = ParameterTuningJobSerializer(
            data=data, context={"request": request}
        )
        assert not serializer.is_valid()
        assert "tried_algorithms_json" in serializer.errors

    def test_tried_algorithms_json_not_array(self, user, factory):
        """JSON that is not an array of strings should be rejected."""
        from recotem.api.serializers.tuning_job import ParameterTuningJobSerializer

        request = factory.post("/api/v1/parameter_tuning_job/")
        request.user = user
        data = {
            "data": 1,
            "split": 1,
            "evaluation": 1,
            "tried_algorithms_json": {"key": "value"},
        }
        serializer = ParameterTuningJobSerializer(
            data=data, context={"request": request}
        )
        assert not serializer.is_valid()
        assert "tried_algorithms_json" in serializer.errors

    def test_tried_algorithms_json_array_of_non_strings(self, user, factory):
        """JSON array containing non-strings should be rejected."""
        from recotem.api.serializers.tuning_job import ParameterTuningJobSerializer

        request = factory.post("/api/v1/parameter_tuning_job/")
        request.user = user
        data = {
            "data": 1,
            "split": 1,
            "evaluation": 1,
            "tried_algorithms_json": [1, 2, 3],
        }
        serializer = ParameterTuningJobSerializer(
            data=data, context={"request": request}
        )
        assert not serializer.is_valid()
        assert "tried_algorithms_json" in serializer.errors

    def test_tried_algorithms_json_null_accepted(self, user, factory):
        """Null tried_algorithms_json should be accepted."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from recotem.api.models import TrainingData
        from recotem.api.serializers.tuning_job import ParameterTuningJobSerializer

        p = Project.objects.create(
            name="NullAlgo", user_column="u", item_column="i", owner=user
        )
        td = TrainingData.objects.create(
            project=p,
            file=SimpleUploadedFile("data.csv", b"u,i\n1,2\n", content_type="text/csv"),
        )
        sc = SplitConfig.objects.create(name="s", created_by=user)
        ec = EvaluationConfig.objects.create(name="e", created_by=user)

        request = factory.post("/api/v1/parameter_tuning_job/")
        request.user = user
        data = {
            "data": td.id,
            "split": sc.id,
            "evaluation": ec.id,
            "n_trials": 2,
            "tried_algorithms_json": None,
        }
        serializer = ParameterTuningJobSerializer(
            data=data, context={"request": request}
        )
        assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
class TestProjectSerializerValidation:
    """Additional validation edge cases for ProjectSerializer."""

    def test_blank_name_rejected(self, user, factory):
        """Empty project name should be rejected."""
        request = factory.post("/api/v1/project/")
        request.user = user
        data = {"name": "", "user_column": "u", "item_column": "i"}
        serializer = ProjectSerializer(data=data, context={"request": request})
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_missing_user_column_rejected(self, user, factory):
        """Missing user_column should be rejected."""
        request = factory.post("/api/v1/project/")
        request.user = user
        data = {"name": "NoUserCol", "item_column": "i"}
        serializer = ProjectSerializer(data=data, context={"request": request})
        assert not serializer.is_valid()
        assert "user_column" in serializer.errors

    def test_missing_item_column_rejected(self, user, factory):
        """Missing item_column should be rejected."""
        request = factory.post("/api/v1/project/")
        request.user = user
        data = {"name": "NoItemCol", "user_column": "u"}
        serializer = ProjectSerializer(data=data, context={"request": request})
        assert not serializer.is_valid()
        assert "item_column" in serializer.errors
