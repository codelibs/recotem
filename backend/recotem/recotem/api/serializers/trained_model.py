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
        from django.conf import settings
        from django_celery_results.models import TaskResult

        from recotem.api.tasks import task_train_recommender, train_recommender_func

        # Check if we're in a test environment
        is_testing = (
            hasattr(settings, "CELERY_TASK_ALWAYS_EAGER")
            and settings.CELERY_TASK_ALWAYS_EAGER
        ) or (
            "sqlite" in settings.DATABASE_URL.lower()
            or "memory" in str(getattr(settings, "CELERY_BROKER_URL", ""))
        )

        if is_testing:
            # Execute training directly in test environment
            task_result, _ = TaskResult.objects.get_or_create(
                task_id=f"test-train-model-{obj.id}", defaults={"status": "STARTED"}
            )

            try:
                train_recommender_func(task_result, obj.id)
                task_result.status = "SUCCESS"
                task_result.save()
            except Exception as e:
                task_result.status = "FAILURE"
                task_result.result = str(e)
                task_result.save()
                raise
        else:
            # Use Celery in production
            task_train_recommender.delay(obj.id)

        return obj
