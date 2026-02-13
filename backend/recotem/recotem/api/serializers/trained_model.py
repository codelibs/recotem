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

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        data_loc = attrs.get("data_loc")
        configuration = attrs.get("configuration")
        if user is None:
            return attrs
        if data_loc is not None and data_loc.project.owner_id not in (None, user.id):
            raise serializers.ValidationError({"data_loc": ["Data not found."]})
        if configuration is not None and configuration.project.owner_id not in (
            None,
            user.id,
        ):
            raise serializers.ValidationError(
                {"configuration": ["Model configuration not found."]}
            )
        # Enforce API key project scope
        api_key = getattr(request, "api_key", None)
        if (
            api_key is not None
            and data_loc is not None
            and data_loc.project_id != api_key.project_id
        ):
            raise serializers.ValidationError({"data_loc": ["Data not found."]})
        if (
            api_key is not None
            and configuration is not None
            and configuration.project_id != api_key.project_id
        ):
            raise serializers.ValidationError(
                {"configuration": ["Model configuration not found."]}
            )
        # Cross-project integrity: check against instance for partial updates
        effective_data_loc = data_loc or getattr(self.instance, "data_loc", None)
        effective_config = configuration or getattr(
            self.instance, "configuration", None
        )
        if (
            effective_data_loc is not None
            and effective_config is not None
            and effective_data_loc.project_id != effective_config.project_id
        ):
            raise serializers.ValidationError(
                {
                    "configuration": [
                        "Model configuration must belong to the same"
                        " project as the training data."
                    ]
                }
            )
        return attrs

    def create(self, validated_data):
        obj: TrainedModel = TrainedModel.objects.create(**validated_data)
        from recotem.api.tasks import task_train_recommender

        task_train_recommender.delay(obj.id)
        return obj
