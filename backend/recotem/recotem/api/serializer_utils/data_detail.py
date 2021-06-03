from pathlib import Path

from rest_framework import exceptions, serializers

from ..models import Project, TrainingData


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
        try:
            obj.validate_return_df()
        except exceptions.ValidationError as e:
            obj.delete()
            raise e
        return obj


class TrainingDataSummarySerializer(serializers.ModelSerializer):
    basename = serializers.SerializerMethodField()
    filesize = serializers.SerializerMethodField()
