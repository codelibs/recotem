from rest_framework import viewsets

from .models import (
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TaskLog,
    TrainedModel,
    TrainingData,
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
)


class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    filterset_fields = ["id", "name"]


class TrainedModelViewset(viewsets.ModelViewSet):
    queryset = TrainedModel.objects.all()
    serializer_class = TrainedModelSerializer
    filterset_fields = ["id", "data_loc", "data_loc__project"]


class TrainingDataViewset(viewsets.ModelViewSet):
    queryset = TrainingData.objects.all()
    serializer_class = TrainingDataSerializer
    filterset_fields = ["id", "project"]


class ModelConfigurationViewset(viewsets.ModelViewSet):
    queryset = ModelConfiguration.objects.all()
    serializer_class = ModelConfigurationSerializer
    filterset_fields = ["id", "project"]


class SplitConfigViewSet(viewsets.ModelViewSet):
    queryset = SplitConfig.objects.all()
    serializer_class = SplitConfigSerializer
    filterset_fields = ["id", "name"]


class EvaluationConfigViewSet(viewsets.ModelViewSet):
    queryset = EvaluationConfig.objects.all()
    serializer_class = EvaluationConfigSerializer
    filterset_fields = ["id", "name"]


class ParameterTuningJobViewSet(viewsets.ModelViewSet):
    queryset = ParameterTuningJob.objects.all()
    serializer_class = ParameterTuningJobSerializer
    filterset_fields = ["id", "project"]


class TaskLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TaskLog.objects.all()
    serializer_class = TaskLogSerializer
    filterset_fields = ["id", "task__taskandparameterjoblink__job"]
