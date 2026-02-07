from django.db import connections, models as db_models
from django.db.utils import ConnectionDoesNotExist
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.exceptions import APIException
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
    SplitConfig,
    TaskLog,
    TrainingData,
)
from recotem.api.serializers import (
    EvaluationConfigSerializer,
    ModelConfigurationSerializer,
    ParameterTuningJobSerializer,
    PingSerializer,
    ProjectSummarySerializer,
    SplitConfigSerializer,
    TaskLogSerializer,
    TrainingDataSerializer,
)
from recotem.api.serializers.data import ItemMetaDataSerializer
from recotem.api.services.project_service import get_project_or_404, get_project_summary

from .filemixin import FileDownloadRemoveMixin
from .model import TrainedModelViewset
from .project import ProjectViewSet


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class TrainingDataViewset(viewsets.ModelViewSet, FileDownloadRemoveMixin):
    permission_classes = [IsAuthenticated]

    serializer_class = TrainingDataSerializer
    filterset_fields = ["id", "project"]
    parser_classes = [MultiPartParser]
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user
        return (
            TrainingData.objects.select_related("project")
            .filter(
                filesize__isnull=False,
            )
            .filter(
                db_models.Q(project__owner=user) | db_models.Q(project__owner__isnull=True)
            )
            .order_by("-ins_datetime")
        )


class ItemMetaDataViewset(viewsets.ModelViewSet, FileDownloadRemoveMixin):
    permission_classes = [IsAuthenticated]

    serializer_class = ItemMetaDataSerializer
    filterset_fields = ["id", "project"]
    parser_classes = [MultiPartParser]
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user
        return (
            ItemMetaData.objects.select_related("project")
            .filter(filesize__isnull=False)
            .filter(
                db_models.Q(project__owner=user) | db_models.Q(project__owner__isnull=True)
            )
            .order_by("-ins_datetime")
        )


class ModelConfigurationViewset(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    serializer_class = ModelConfigurationSerializer
    filterset_fields = ["id", "project"]
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user
        return (
            ModelConfiguration.objects.select_related("project")
            .filter(
                db_models.Q(project__owner=user) | db_models.Q(project__owner__isnull=True)
            )
            .order_by("-id")
        )


class SplitConfigFilter(filters.FilterSet):
    unnamed = filters.BooleanFilter(field_name="name", lookup_expr="isnull")

    class Meta:
        model = SplitConfig
        fields = ["name", "id", "unnamed"]


class SplitConfigViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    serializer_class = SplitConfigSerializer
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = SplitConfigFilter
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user
        return (
            SplitConfig.objects.filter(
                db_models.Q(created_by=user) | db_models.Q(created_by__isnull=True)
            )
            .select_related("created_by")
            .order_by("-ins_datetime")
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class EvaluationConfigFilter(filters.FilterSet):
    unnamed = filters.BooleanFilter(field_name="name", lookup_expr="isnull")

    class Meta:
        model = EvaluationConfig
        fields = ["name", "id", "unnamed"]


class EvaluationConfigViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    serializer_class = EvaluationConfigSerializer
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = EvaluationConfigFilter
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user
        return (
            EvaluationConfig.objects.filter(
                db_models.Q(created_by=user) | db_models.Q(created_by__isnull=True)
            )
            .select_related("created_by")
            .order_by("-ins_datetime")
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ParameterTuningJobViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    serializer_class = ParameterTuningJobSerializer
    filterset_fields = ["id", "data__project", "data"]
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user
        return (
            ParameterTuningJob.objects.select_related(
                "data", "data__project", "split", "evaluation", "best_config", "tuned_model"
            )
            .prefetch_related("task_links")
            .filter(
                db_models.Q(data__project__owner=user)
                | db_models.Q(data__project__owner__isnull=True)
            )
            .order_by("-ins_datetime")
        )


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

    serializer_class = TaskLogSerializer
    filterset_class = TaskLogFilter
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user
        return (
            TaskLog.objects.select_related("task")
            .filter(
                db_models.Q(task__tuning_job_link__job__data__project__owner=user)
                | db_models.Q(
                    task__tuning_job_link__job__data__project__owner__isnull=True
                )
                | db_models.Q(task__model_link__model__data_loc__project__owner=user)
                | db_models.Q(
                    task__model_link__model__data_loc__project__owner__isnull=True
                )
            )
            .distinct()
            .order_by("-id")
        )


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
    def get(self, request, pk: int, format=None):
        project_obj = get_project_or_404(pk, user=request.user)
        summary = get_project_summary(project_obj)
        serializer = ProjectSummarySerializer(summary)
        return Response(serializer.data)
