from rest_framework import serializers

from recotem.api.models import ABTest


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

    def validate_project(self, project):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and project.owner_id not in (None, user.id):
            raise serializers.ValidationError("Project not found.")
        return project


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
