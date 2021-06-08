import re
from pathlib import PurePath
from typing import Optional

from rest_framework import exceptions, serializers

from recotem.api.models import Project, TrainingData


class TrainingDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainingData
        fields = [
            "id",
            "project",
            "upload_path",
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
        obj: TrainingData = TrainingData.objects.create(**validated_data)
        try:
            obj.validate_return_df()
        except exceptions.ValidationError as e:
            obj.delete()
            raise e
        return obj
