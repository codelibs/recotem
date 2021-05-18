from django.db.models import fields
from typing import Optional, Any
from rest_framework import serializers
from .tasks import execute_irspack
from .utils import read_dataframe
from django_celery_results.models import TaskResult

from .models import (
    EvaluationConfig,
    Project,
    SplitConfig,
    TrainingData,
    ParameterTuningJob,
    TrainedModel,
    TaskLog,
)
from django.core.files.uploadedfile import UploadedFile
import pandas as pd
from rest_framework.exceptions import ValidationError
from pathlib import Path


class TrainedModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainedModel
        fields = "__all__"


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = "__all__"


class TrainingDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainingData
        fields = "__all__"

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


class ParameterTuningJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterTuningJob
        fields = "__all__"

    def create(self, validated_data):
        obj: ParameterTuningJob = ParameterTuningJob.objects.create(**validated_data)
        for _ in range(obj.n_tasks_parallel):
            task = execute_irspack.delay(obj.id)
        return obj
