from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from recotem.api.models import ParameterTuningJob, TaskAndParameterJobLink, TrainingData
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
            "status",
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
        read_only_fields = ["ins_datetime", "task_links", "status"]

    def validate_tried_algorithms_json(self, value):
        if value is None:
            return value
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise serializers.ValidationError("Must be a JSON array of strings.")
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        data = attrs.get("data")
        split = attrs.get("split")
        evaluation = attrs.get("evaluation")
        if user is None:
            return attrs
        errors: dict[str, list[str]] = {}
        if data is not None and data.project.owner_id not in (None, user.id):
            errors["data"] = ["Data not found."]
        if split is not None and split.created_by_id not in (None, user.id):
            errors["split"] = ["Split config not found."]
        if evaluation is not None and evaluation.created_by_id not in (None, user.id):
            errors["evaluation"] = ["Evaluation config not found."]
        if errors:
            raise ValidationError(errors)
        return attrs

    def create(self, validated_data):
        data: TrainingData = validated_data["data"]
        if data.filesize is None:
            raise ValidationError(dict(data=[f"Data {data.pk} has been deleted."]))
        obj: ParameterTuningJob = ParameterTuningJob.objects.create(**validated_data)
        from recotem.api.tasks import start_tuning_job

        transaction.on_commit(lambda: start_tuning_job(obj))
        return obj
