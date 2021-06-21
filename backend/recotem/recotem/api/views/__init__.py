from rest_framework import viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from recotem.api.models import (
    EvaluationConfig,
    ItemMetaData,
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
from recotem.api.serializers.data import ItemMetaDataSerializer

from .filemixin import FileDownloadRemoveMixin
from .model import TrainedModelViewset
from .project import ProjectSummaryViewSet, ProjectViewSet


class TrainingDataViewset(viewsets.ModelViewSet, FileDownloadRemoveMixin):
    permission_classes = [IsAuthenticated]

    queryset = (
        TrainingData.objects.all()
        .filter(filesize__isnull=False)
        .order_by("-ins_datetime")
    )
    serializer_class = TrainingDataSerializer
    filterset_fields = ["id", "project"]

    class pagination_class(PageNumberPagination):
        page_size = 10
        page_size_query_param = "page_size"


class ItemMetaDataViewset(viewsets.ModelViewSet, FileDownloadRemoveMixin):
    permission_classes = [IsAuthenticated]

    queryset = (
        ItemMetaData.objects.all()
        .filter(filesize__isnull=False)
        .order_by("-ins_datetime")
    )
    serializer_class = ItemMetaDataSerializer
    filterset_fields = ["id", "project"]

    class pagination_class(PageNumberPagination):
        page_size = 10
        page_size_query_param = "page_size"


class ModelConfigurationViewset(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = ModelConfiguration.objects.all()
    serializer_class = ModelConfigurationSerializer
    filterset_fields = ["id", "project"]


class SplitConfigViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = SplitConfig.objects.all().filter(name__isnull=False)
    serializer_class = SplitConfigSerializer
    filterset_fields = ["id", "name"]


class EvaluationConfigViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = EvaluationConfig.objects.all().filter(name__isnull=False)
    serializer_class = EvaluationConfigSerializer
    filterset_fields = ["id", "name"]


class ParameterTuningJobViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = ParameterTuningJob.objects.all()
    serializer_class = ParameterTuningJobSerializer
    filterset_fields = ["id", "data__project", "data"]

    class pagination_class(PageNumberPagination):
        page_size = 10
        page_size_query_param = "page_size"


class TaskLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = TaskLog.objects.all()
    serializer_class = TaskLogSerializer
    filterset_fields = [
        "id",
        "task__tuning_job_link__job",
        "task__model_link__model",
    ]
