from rest_framework import serializers

from recotem.api.models import (
    EvaluationConfig,
    ModelConfiguration,
    SplitConfig,
    TaskLog,
)

from .auth import UserDetailsSerializer
from .data import TrainingDataSerializer
from .ping import PingSerializer
from .project import ProjectSerializer, ProjectSummarySerializer
from .trained_model import TrainedModelSerializer
from .tuning_job import ParameterTuningJobSerializer


class SplitConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = SplitConfig
        fields = "__all__"
        read_only_fields = ["created_by"]

    def validate_heldout_ratio(self, value):
        value_f = float(value)
        if value_f < 0 or value_f > 1.0:
            raise serializers.ValidationError("heldout_ratio must be in [0.0, 1.0]")
        return value_f

    def validate_test_user_ratio(self, value):
        value_f = float(value)
        if value_f < 0 or value_f > 1.0:
            raise serializers.ValidationError("test_user_ratio must be in [0.0, 1.0]")
        return value_f


class EvaluationConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvaluationConfig
        fields = "__all__"
        read_only_fields = ["created_by"]


class TaskLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskLog
        fields = "__all__"


class ModelConfigurationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelConfiguration
        fields = [
            "id",
            "tuning_job",
            "name",
            "project",
            "recommender_class_name",
            "parameters_json",
            "ins_datetime",
        ]
        read_only_fields = ["tuning_job", "ins_datetime"]

    def validate_parameters_json(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Must be a JSON object (dict).")
        return value

    def validate_recommender_class_name(self, value):
        import re

        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value):
            raise serializers.ValidationError(
                "Must be a valid Python identifier (letters, digits, underscores)."
            )

        from irspack.recommenders.base import get_recommender_class

        try:
            get_recommender_class(value)
        except ValueError:
            raise serializers.ValidationError(
                f"'{value}' is not a valid irspack recommender class."
            ) from None
        return value

    def validate_project(self, project):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and project.owner_id not in (None, user.id):
            raise serializers.ValidationError("Project not found.")
        return project


__all__ = (
    "TrainingDataSerializer",
    "ProjectSerializer",
    "ProjectSummarySerializer",
    "ParameterTuningJobSerializer",
    "TrainedModelSerializer",
    "SplitConfigSerializer",
    "EvaluationConfigSerializer",
    "TaskLogSerializer",
    "ModelConfigurationSerializer",
    "UserDetailsSerializer",
    "PingSerializer",
)
