from rest_framework import serializers

from recotem.api.models import ApiKey

VALID_SCOPES = ["read", "write", "predict"]


class ApiKeySerializer(serializers.ModelSerializer):
    """Serializer for listing/retrieving API keys (never exposes hashed_key)."""

    class Meta:
        model = ApiKey
        fields = [
            "id",
            "project",
            "name",
            "key_prefix",
            "scopes",
            "is_active",
            "expires_at",
            "last_used_at",
            "ins_datetime",
        ]
        read_only_fields = [
            "id",
            "key_prefix",
            "is_active",
            "last_used_at",
            "ins_datetime",
        ]


class ApiKeyCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating API keys. Returns the full key once."""

    key = serializers.CharField(read_only=True)

    class Meta:
        model = ApiKey
        fields = [
            "id",
            "project",
            "name",
            "scopes",
            "expires_at",
            "key",
            "ins_datetime",
        ]
        read_only_fields = ["id", "key", "ins_datetime"]

    def validate_scopes(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Scopes must be a list.")
        for scope in value:
            if scope not in VALID_SCOPES:
                raise serializers.ValidationError(
                    f"Invalid scope '{scope}'. Valid scopes: {VALID_SCOPES}"
                )
        if not value:
            raise serializers.ValidationError("At least one scope is required.")
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
