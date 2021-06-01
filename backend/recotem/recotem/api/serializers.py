from pathlib import Path
from typing import Any

from rest_framework import serializers

from .models import (
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TaskLog,
    TrainedModel,
    TrainingData,
    User,
)
from .tasks import start_tuning_job


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["username"]


class TrainedModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainedModel
        fields = "__all__"


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = "__all__"


class TrainingDataSerializer(serializers.ModelSerializer):
    basename = serializers.SerializerMethodField()
    filesize = serializers.SerializerMethodField()

    class Meta:
        model = TrainingData
        fields = [
            "id",
            "project",
            "upload_path",
            "ins_datetime",
            "upd_datetime",
            "basename",
            "filesize",
        ]
        read_only_fields = [
            "ins_datetime",
            "upd_datetime",
            "basename",
            "filesize",
        ]

    def get_basename(self, instance: TrainingData) -> str:
        return Path(instance.upload_path.name).name

    def get_filesize(self, instance: TrainingData) -> int:
        return instance.upload_path.size

    def create(self, validated_data):
        obj: TrainingData = TrainingData.objects.create(**validated_data)
        obj.validate_return_df()
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


class ParameterTuningJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterTuningJob
        fields = "__all__"
