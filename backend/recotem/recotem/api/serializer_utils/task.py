from django_celery_results.models import TaskResult
from rest_framework import serializers

from recotem.api.models import TaskLog


class TaskLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskLog
        fields = ["content"]


class TaskResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskResult
        fields = ["task_id", "status", "date_created", "date_done"]
