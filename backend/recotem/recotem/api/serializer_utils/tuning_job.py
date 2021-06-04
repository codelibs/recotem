from rest_framework import serializers

from recotem.api.models import ParameterTuningJob, TaskAndParameterJobLink
from recotem.api.serializer_utils.task import TaskResultSerializer


class ParameterTuningJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterTuningJob
        fields = "__all__"


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
