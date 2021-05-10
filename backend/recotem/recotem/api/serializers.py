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
)
from django.core.files.uploadedfile import UploadedFile
import pandas as pd
from rest_framework.exceptions import ValidationError
from pathlib import Path
from pandas.errors import ParserError
from pickle import UnpicklingError


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = "__all__"


class TrainingDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainingData
        fields = "__all__"

    def create(self, validated_data):

        upload_path: UploadedFile = validated_data["upload_path"]
        pathname = Path(upload_path.name)
        df = read_dataframe(pathname, upload_path)
        obj: TrainingData = TrainingData.objects.create(**validated_data)
        time_column: Optional[str] = obj.project.time_column
        if time_column is not None:
            try:
                df[time_column] = pd.to_datetime(df[time_column])
            except ValueError:
                raise ValidationError(f"Could not interpret {time_column} as datetime.")

        user_column: str = obj.project.user_column
        item_column: str = obj.project.item_column
        time_column: Optional[str] = obj.project.time_column

        if user_column not in df:
            obj.delete()
            raise ValidationError(
                f'Column "{user_column}" not found in the upload file.'
            )
        if item_column not in df:
            obj.delete()
            raise ValidationError(
                f'Column "{item_column}" not found in the upload file.'
            )
        if time_column is not None:
            obj.delete()
            if time_column not in df:
                raise ValidationError(
                    f'Column "{time_column}" not found in the upload file.'
                )
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


class ParameterTuningJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterTuningJob
        fields = "__all__"

    def create(self, validated_data):
        obj: ParameterTuningJob = ParameterTuningJob.objects.create(**validated_data)
        task = execute_irspack.delay(obj.id)
        task_result, _ = TaskResult.objects.get_or_create(task_id=task.task_id)
        obj.task_result = task_result
        obj.save()
        return obj
