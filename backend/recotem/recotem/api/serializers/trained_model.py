from rest_framework import serializers

from ..models import TaskAndTrainedModelLink, TrainedModel
from .task import TaskResultSerializer


class TaskAndTrainedModelLinkSerializer(serializers.ModelSerializer):
    task = TaskResultSerializer()

    class Meta:
        model = TaskAndTrainedModelLink
        fields = ["task"]


class TrainedModelSerializer(serializers.ModelSerializer):

    task_links = TaskAndTrainedModelLinkSerializer(many=True, read_only=True)

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
            "task_links",
        ]
        read_only_fields = ["ins_datetime", "basename", "filesize", "task_links"]

    def create(self, validated_data):
        obj: TrainedModel = TrainedModel.objects.create(**validated_data)
        from recotem.api.tasks import task_train_recommender

        task_train_recommender.delay(obj.id)
        return obj
