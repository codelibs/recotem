from typing import Any

from django.contrib.auth import get_user_model
from rest_framework import serializers

from recotem.api.models import (
    EvaluationConfig,
    ModelConfiguration,
    SplitConfig,
    TaskLog,
    TrainedModel,
)
from recotem.api.serializer_utils import (
    ParameterTuningJobSerializer,
    ProjectSerializer,
    TrainingDataSerializer,
)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["username", "is_staff", "is_superuser"]


class TrainedModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainedModel
        fields = [
            "id",
            "configuration",
            "data_loc",
            "file",
            "irspack_version",
            "ins_datetime",
            "basename",
            "filesize",
        ]
        read_only_fields = [
            "ins_datetime",
            "basename",
            "filesize",
        ]

    def create(self, validated_data):
        obj: TrainedModel = TrainedModel.objects.create(**validated_data)
        from recotem.api.tasks import task_train_recommender

        task_train_recommender.delay(obj.id)
        return obj


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
