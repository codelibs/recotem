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


class ProjectSummarySerializer(serializers.ModelSerializer):
    trainingdata_set = TrainingDataForSummarySerializer(many=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "user_column",
            "item_column",
            "time_column",
            "ins_datetime",
            "trainingdata_set",
        ]
