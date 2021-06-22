from typing import Any

from django.contrib.auth import get_user_model
from rest_framework import serializers

from recotem.api.models import (
    EvaluationConfig,
    ModelConfiguration,
    SplitConfig,
    TaskLog,
)

from .data import TrainingDataSerializer
from .project import ProjectSerializer, ProjectSummarySerializer
from .trained_model import TrainedModelSerializer
from .tuning_job import ParameterTuningJobSerializer


class SplitConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = SplitConfig
        fields = "__all__"

    def validate_heldout_ratio(self, value: Any):
        value_f = float(value)
        if value_f < 0 or value_f > 1.0:
            raise serializers.ValidationError("heldout_ratio must be in [0.0, 1.0]")
        return value_f

    def validate_test_user_ratio(self, value: Any):
        value_f = float(value)
        if value_f < 0 or value_f > 1.0:
            raise serializers.ValidationError("test_user_ratio must be in [0.0, 1.0]")
        return value_f


class EvaluationConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvaluationConfig
        fields = "__all__"


class TaskLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskLog
        fields = "__all__"


class ModelConfigurationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelConfiguration
        fields = "__all__"


__all__ = (
    "TrainingDataSerializer",
    "ProjectSerializer",
    "ProjectSummarySerializer" "ParameterTuningJobSerializer",
    "TrainedModelSerializer",
    "SplitConfigSerializer",
    "EvaluationConfigSerializer",
    "TaskLogSerializer",
    "ModelConfigurationSerializer",
)
