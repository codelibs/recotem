from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated

from .models import (
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TaskLog,
    TrainedModel,
    TrainingData,
    User,
)
from .serializers import (
    EvaluationConfigSerializer,
    ModelConfigurationSerializer,
    ParameterTuningJobSerializer,
    ProjectSerializer,
    SplitConfigSerializer,
    TaskLogSerializer,
    TrainedModelSerializer,
    TrainingDataSerializer,
    UserSerializer,
)


class GetMeViewset(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = User.objects.all()
    serializer_class = UserSerializer


class ProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    filterset_fields = ["id", "name"]


class TrainedModelViewset(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = TrainedModel.objects.all()
    serializer_class = TrainedModelSerializer
    filterset_fields = ["id", "data_loc", "data_loc__project"]


class TrainingDataViewset(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = TrainingData.objects.all()
    serializer_class = TrainingDataSerializer
    filterset_fields = ["id", "project"]


class ModelConfigurationViewset(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = ModelConfiguration.objects.all()
    serializer_class = ModelConfigurationSerializer
    filterset_fields = ["id", "project"]


class SplitConfigViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = SplitConfig.objects.all()
    serializer_class = SplitConfigSerializer
    filterset_fields = ["id", "name"]


class EvaluationConfigViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = EvaluationConfig.objects.all()
    serializer_class = EvaluationConfigSerializer
    filterset_fields = ["id", "name"]


class ParameterTuningJobViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = ParameterTuningJob.objects.all()
    serializer_class = ParameterTuningJobSerializer
    filterset_fields = ["id", "data__project"]


class TaskLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = TaskLog.objects.all()
    serializer_class = TaskLogSerializer
    filterset_fields = [
        "id",
        "task__taskandparameterjoblink__job",
        "task__taskandtrainedmodellink__model",
    ]
