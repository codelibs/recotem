from rest_framework import serializers

from recotem.api.models import ParameterTuningJob, TaskAndParameterJobLink
from recotem.api.serializer_utils.task import TaskResultSerializer


class ParameterTuningJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterTuningJob
        fields = "__all__"

    def create(self, validated_data):
        obj: ParameterTuningJob = ParameterTuningJob.objects.create(**validated_data)
        from recotem.api.tasks import start_tuning_job

        start_tuning_job(obj)
        return obj


class TaskAndParameterJobLinkSerializer(serializers.ModelSerializer):
    task = TaskResultSerializer()

    class Meta:
        model = TaskAndParameterJobLink
        fields = ["task"]


class ParameterTuningJobListSerializer(serializers.ModelSerializer):
    taskandparameterjoblink_set = TaskAndParameterJobLinkSerializer(many=True)

    class Meta:
        model = ParameterTuningJob
        fields = ["id", "taskandparameterjoblink_set", "ins_datetime", "name", "data"]
