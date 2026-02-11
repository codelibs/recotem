from rest_framework import serializers

from recotem.api.models import ConversionEvent


class ConversionEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversionEvent
        fields = [
            "id",
            "project",
            "deployment_slot",
            "user_id",
            "item_id",
            "event_type",
            "recommendation_request_id",
            "timestamp",
            "metadata_json",
        ]
        read_only_fields = ["id", "timestamp"]

    def validate_event_type(self, value):
        valid = ["impression", "click", "purchase"]
        if value not in valid:
            raise serializers.ValidationError(
                f"Invalid event type. Must be one of: {valid}"
            )
        return value


class ConversionEventBatchSerializer(serializers.Serializer):
    events = ConversionEventSerializer(many=True)

    def create(self, validated_data):
        events_data = validated_data["events"]
        events = [ConversionEvent(**data) for data in events_data]
        return ConversionEvent.objects.bulk_create(events)
