from rest_framework import serializers

from recotem.api.models import Project, TrainingData


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = "__all__"


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
