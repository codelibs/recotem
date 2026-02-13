from rest_framework import serializers

from recotem.api.models import DeploymentSlot


class DeploymentSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentSlot
        fields = [
            "id",
            "project",
            "name",
            "trained_model",
            "weight",
            "is_active",
            "ins_datetime",
            "updated_at",
        ]
        read_only_fields = ["id", "ins_datetime", "updated_at"]

    def validate_weight(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError("Weight must be between 0 and 100.")
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
        trained_model = data.get(
            "trained_model", getattr(self.instance, "trained_model", None)
        )
        project = data.get("project", getattr(self.instance, "project", None))
        if (
            trained_model is not None
            and project is not None
            and trained_model.data_loc.project_id != project.id
        ):
            raise serializers.ValidationError(
                {"trained_model": "Trained model does not belong to this project."}
            )
        return data
