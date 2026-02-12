from rest_framework import serializers

from recotem.api.models import RetrainingRun, RetrainingSchedule


class RetrainingScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = RetrainingSchedule
        fields = [
            "id",
            "project",
            "is_enabled",
            "cron_expression",
            "training_data",
            "model_configuration",
            "retune",
            "split_config",
            "evaluation_config",
            "max_retries",
            "notify_on_failure",
            "last_run_at",
            "last_run_status",
            "next_run_at",
            "auto_deploy",
            "ins_datetime",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "last_run_at",
            "last_run_status",
            "next_run_at",
            "ins_datetime",
            "updated_at",
        ]

    def validate_cron_expression(self, value):
        parts = value.strip().split()
        if len(parts) != 5:
            raise serializers.ValidationError(
                "Cron expression must have exactly 5 fields."
            )
        return value

    def validate_project(self, project):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and project.owner_id not in (None, user.id):
            raise serializers.ValidationError("Project not found.")
        api_key = getattr(request, "api_key", None)
        if api_key is not None and api_key.project_id != project.id:
            raise serializers.ValidationError("Project not found.")
        return project


class RetrainingRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = RetrainingRun
        fields = [
            "id",
            "schedule",
            "status",
            "trained_model",
            "tuning_job",
            "error_message",
            "ins_datetime",
            "completed_at",
            "data_rows_at_trigger",
        ]
        read_only_fields = fields
