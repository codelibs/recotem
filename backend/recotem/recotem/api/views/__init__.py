from typing import Optional

from django.db import connections
from django.db.utils import ConnectionDoesNotExist
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.exceptions import APIException, NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from recotem.api.models import (
    EvaluationConfig,
    ItemMetaData,
    ModelConfiguration,
    ParameterTuningJob,
    Project,
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
    TrainingDataSerializer,
    PingSerializer,
    ProjectSummarySerializer,
)
from recotem.api.serializers.data import ItemMetaDataSerializer

from .filemixin import FileDownloadRemoveMixin
from .model import TrainedModelViewset
from .project import ProjectViewSet


class TrainingDataViewset(viewsets.ModelViewSet, FileDownloadRemoveMixin):
    permission_classes = [IsAuthenticated]

    queryset = (
        TrainingData.objects.all()
        .filter(filesize__isnull=False)
        .order_by("-ins_datetime")
    )
    serializer_class = TrainingDataSerializer
    filterset_fields = ["id", "project"]
    parser_classes = [MultiPartParser]

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
    parser_classes = [MultiPartParser]

    class pagination_class(PageNumberPagination):
        page_size = 5
        page_size_query_param = "page_size"


class ModelConfigurationViewset(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = ModelConfiguration.objects.all().order_by("-id")
    serializer_class = ModelConfigurationSerializer
    filterset_fields = ["id", "project"]

    class pagination_class(PageNumberPagination):
        page_size = 10
        page_size_query_param = "page_size"


class SplitConfigFilter(filters.FilterSet):
    unnamed = filters.BooleanFilter(field_name="name", lookup_expr="isnull")

    class Meta:
        model = SplitConfig
        fields = ["name", "id", "unnamed"]


class SplitConfigViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = SplitConfig.objects.all()
    serializer_class = SplitConfigSerializer
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = SplitConfigFilter


class EvaluationConfigFilter(filters.FilterSet):
    unnamed = filters.BooleanFilter(field_name="name", lookup_expr="isnull")

    class Meta:
        model = EvaluationConfig
        fields = ["name", "id", "unnamed"]


class EvaluationConfigViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = EvaluationConfig.objects.all().filter()
    serializer_class = EvaluationConfigSerializer
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = EvaluationConfigFilter


class ParameterTuningJobViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = ParameterTuningJob.objects.all().order_by("-ins_datetime")
    serializer_class = ParameterTuningJobSerializer
    filterset_fields = ["id", "data__project", "data"]

    class pagination_class(PageNumberPagination):
        page_size = 10
        page_size_query_param = "page_size"


class TaskLogFilter(filters.FilterSet):
    id_gt = filters.NumberFilter(field_name="id", lookup_expr="gt")
    tuning_job_id = filters.NumberFilter(field_name="task__tuning_job_link__job")
    model_id = filters.NumberFilter(field_name="task__model_link__model")

    class Meta:
        model = TaskLog
        fields = [
            "id",
            "tuning_job_id",
            "model_id",
            "id_gt",
        ]


class TaskLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    queryset = TaskLog.objects.all()
    serializer_class = TaskLogSerializer
    filterset_class = TaskLogFilter


class PingView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(responses={200: PingSerializer})
    def get(self, request):
        try:
            _ = connections["default"]
            return Response(dict(success=True))
        except ConnectionDoesNotExist:
            raise APIException(detail=dict(success=False), code=400)


class ProjectSummaryView(APIView):
    @extend_schema(responses={200: ProjectSummarySerializer})
    def get(self, request, pk: int, format: Optional[str] = None):
        try:
            project_obj: Project = Project.objects.get(pk=pk)
        except Project.DoesNotExist:
            raise NotFound(detail=f"project {pk} not found.")
        n_data = TrainingData.objects.filter(
            project=project_obj, filesize__isnull=False
        ).count()
        n_complete_jobs = ModelConfiguration.objects.filter(
            project=project_obj, tuning_job__isnull=False
        ).count()
        n_models = TrainedModel.objects.filter(
            data_loc__project=project_obj, filesize__isnull=False
        ).count()
        serializer = ProjectSummarySerializer(
            dict(
                n_data=n_data,
                n_complete_jobs=n_complete_jobs,
                n_models=n_models,
                ins_datetime=project_obj.ins_datetime,
            )
        )
        return Response(serializer.data)
