from rest_framework import serializers

from recotem.api.models import ABTest

VALID_TARGET_METRICS = ["ctr", "purchase_rate", "conversion_rate"]


class ABTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ABTest
        fields = [
            "id",
            "project",
            "name",
            "status",
            "control_slot",
            "variant_slot",
            "target_metric_name",
            "min_sample_size",
            "confidence_level",
            "started_at",
            "ended_at",
            "winner_slot",
            "ins_datetime",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "started_at",
            "ended_at",
            "winner_slot",
            "ins_datetime",
            "updated_at",
        ]

    def validate_confidence_level(self, value):
        if value < 0.5 or value > 0.99:
            raise serializers.ValidationError(
                "Confidence level must be between 0.50 and 0.99."
            )
        return value

    def validate_target_metric_name(self, value):
        if value not in VALID_TARGET_METRICS:
            raise serializers.ValidationError(
                f"Invalid metric '{value}'. "
                f"Valid options: {', '.join(VALID_TARGET_METRICS)}"
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

    def validate(self, data):
        project = data.get("project", getattr(self.instance, "project", None))
        control_slot = data.get(
            "control_slot", getattr(self.instance, "control_slot", None)
        )
        variant_slot = data.get(
            "variant_slot", getattr(self.instance, "variant_slot", None)
        )
        if (
            project is not None
            and control_slot is not None
            and control_slot.project_id != project.id
        ):
            raise serializers.ValidationError(
                {"control_slot": "Control slot does not belong to this project."}
            )
        if (
            project is not None
            and variant_slot is not None
            and variant_slot.project_id != project.id
        ):
            raise serializers.ValidationError(
                {"variant_slot": "Variant slot does not belong to this project."}
            )
        return data


class ABTestResultSerializer(serializers.Serializer):
    control_impressions = serializers.IntegerField()
    control_conversions = serializers.IntegerField()
    control_rate = serializers.FloatField()
    variant_impressions = serializers.IntegerField()
    variant_conversions = serializers.IntegerField()
    variant_rate = serializers.FloatField()
    z_score = serializers.FloatField()
    p_value = serializers.FloatField()
    significant = serializers.BooleanField()
    lift = serializers.FloatField()
    confidence_interval = serializers.ListField(child=serializers.FloatField())
    min_sample_size = serializers.IntegerField()
    sufficient_data = serializers.BooleanField()
