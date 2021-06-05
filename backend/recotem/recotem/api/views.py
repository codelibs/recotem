from typing import Union

from rest_framework import viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated

from recotem.api.models import (
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    SplitConfig,
    TaskLog,
    TrainedModel,
    TrainingData,
)
from recotem.api.serializers import (
    EvaluationConfigSerializer,
    ModelConfigurationSerializer,
    ParameterTuningJobSerializer,
    SplitConfigSerializer,
    TaskLogSerializer,
    TrainedModelSerializer,
    TrainingDataSerializer,
)
from recotem.api.view_utils.getme import GetMeViewset
from recotem.api.view_utils.project import ProjectSummaryViewSet, ProjectViewSet


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

    class pagination_class(PageNumberPagination):
        page_size = 10
        page_size_query_param = "page_size"
        max_page_size = 10000


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
