from rest_framework import serializers

from recotem.api.models import Project, TrainingData


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = "__all__"
        read_only_fields = ["owner"]

    def validate_name(self, value):
        request = self.context.get("request")
        if request and hasattr(request, "user") and request.user.is_authenticated:
            owner = request.user
        else:
            owner = None

        qs = Project.objects.filter(owner=owner, name=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "A project with this name already exists."
            )

        # Legacy projects (owner=None): ensure global uniqueness
        if owner is None:
            qs = Project.objects.filter(owner__isnull=True, name=value)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    "A project with this name already exists."
                )
        return value


class TrainingDataForSummarySerializer(serializers.ModelSerializer):
    n_parameter_tuning_jobs = serializers.IntegerField(
        source="parametertuningjob_set.count"
    )
    n_trained_models = serializers.IntegerField(source="trainedmodel_set.count")

    class Meta:
        model = TrainingData
        fields = ["id", "n_parameter_tuning_jobs", "n_trained_models"]


class ProjectSummarySerializer(serializers.Serializer):
    n_data = serializers.IntegerField()
    n_complete_jobs = serializers.IntegerField()
    n_models = serializers.IntegerField()
    ins_datetime = serializers.DateTimeField()
