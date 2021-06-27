from rest_framework import serializers

from recotem.api.models import ParameterTuningJob, TaskAndParameterJobLink
from recotem.api.serializers.task import TaskResultSerializer


class TaskAndParameterJobLinkSerializer(serializers.ModelSerializer):
    task = TaskResultSerializer()

    class Meta:
        model = TaskAndParameterJobLink
        fields = ["task"]


class ParameterTuningJobSerializer(serializers.ModelSerializer):
    task_links = TaskAndParameterJobLinkSerializer(many=True, read_only=True)

    class Meta:
        model = ParameterTuningJob
        fields = [
            "id",
            "data",
            "split",
            "evaluation",
            "n_tasks_parallel",
            "n_trials",
            "memory_budget",
            "timeout_overall",
            "timeout_singlestep",
            "random_seed",
            "tried_algorithms_json",
            "irspack_version",
            "train_after_tuning",
            "best_score",
            "tuned_model",
            "best_config",
            "ins_datetime",
            "task_links",
        ]
        read_only_fields = ["ins_datetime", "task_links"]

    def create(self, validated_data):
        obj: ParameterTuningJob = ParameterTuningJob.objects.create(**validated_data)
        from recotem.api.tasks import start_tuning_job

        start_tuning_job(obj)
        return obj
