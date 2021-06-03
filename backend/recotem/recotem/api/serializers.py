from pathlib import Path
from typing import Any

from rest_framework import serializers

from .models import (
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    SplitConfig,
    TaskLog,
    TrainedModel,
    TrainingData,
    User,
)
from .serializer_utils import ProjectSerializer, TrainingDataSerializer
from .tasks import start_tuning_job


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["username", "is_staff", "is_superuser"]


class TrainedModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainedModel
        fields = "__all__"


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


class ParameterTuningJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterTuningJob
        fields = "__all__"
