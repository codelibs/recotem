import logging
from pathlib import Path

import pandas as pd
from django.db import connections
from django.db import models as db_models
from django.db.utils import ConnectionDoesNotExist
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
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
from recotem.api.utils import PREVIEW_ROW_LIMIT, read_dataframe

from .ab_test import ABTestViewSet  # noqa: F401
from .api_key import ApiKeyViewSet  # noqa: F401
from .deployment import DeploymentSlotViewSet  # noqa: F401
from .events import ConversionEventViewSet  # noqa: F401
from .filemixin import FileDownloadRemoveMixin
from .mixins import CreatedByResourceMixin, OwnedResourceMixin
from .model import TrainedModelViewset  # noqa: F401
from .pagination import StandardPagination
from .project import ProjectViewSet  # noqa: F401
from .retraining import RetrainingRunViewSet, RetrainingScheduleViewSet  # noqa: F401

logger = logging.getLogger(__name__)


class TrainingDataViewset(
    OwnedResourceMixin, viewsets.ModelViewSet, FileDownloadRemoveMixin
):
    permission_classes = [IsAuthenticated]
    serializer_class = TrainingDataSerializer
    filterset_fields = ["id", "project"]
    parser_classes = [MultiPartParser]
    pagination_class = StandardPagination
    owner_lookup = "project__owner"

    def get_queryset(self):
        return (
            TrainingData.objects.select_related("project")
            .filter(filesize__isnull=False)
            .filter(self.get_owner_filter())
            .order_by("-ins_datetime")
        )

    @extend_schema(
        parameters=[
            inline_serializer(
                "DataPreviewParams",
                fields={
                    "n_rows": drf_serializers.IntegerField(default=50),
                },
            )
        ],
        responses={200: dict},
    )
    @action(detail=True, methods=["get"], url_path="preview")
    def preview(self, request, pk=None):
        """Return the first N rows of the training data as JSON."""
        obj = self.get_object()
        n_rows = min(int(request.query_params.get("n_rows", 50)), PREVIEW_ROW_LIMIT)
        try:
            preview_df = read_dataframe(Path(obj.file.name), obj.file, nrows=n_rows)
            return Response(
                {
                    "columns": list(preview_df.columns),
                    "rows": preview_df.values.tolist(),
                    "total_rows": len(preview_df),
                }
            )
        except (pd.errors.ParserError, ValueError, OSError) as exc:
            logger.debug("Failed to read training data file %s: %s", pk, exc)
            return Response(
                {
                    "columns": [],
                    "rows": [],
                    "total_rows": 0,
                    "error": "Unable to read file",
                },
                status=400,
            )


class ItemMetaDataViewset(
    OwnedResourceMixin, viewsets.ModelViewSet, FileDownloadRemoveMixin
):
    permission_classes = [IsAuthenticated]
    serializer_class = ItemMetaDataSerializer
    filterset_fields = ["id", "project"]
    parser_classes = [MultiPartParser]
    pagination_class = StandardPagination
    owner_lookup = "project__owner"

    def get_queryset(self):
        return (
            ItemMetaData.objects.select_related("project")
            .filter(filesize__isnull=False)
            .filter(self.get_owner_filter())
            .order_by("-ins_datetime")
        )


class ModelConfigurationViewset(OwnedResourceMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ModelConfigurationSerializer
    filterset_fields = ["id", "project"]
    pagination_class = StandardPagination
    owner_lookup = "project__owner"

    def get_queryset(self):
        return (
            ModelConfiguration.objects.select_related("project")
            .filter(self.get_owner_filter())
            .order_by("-id")
        )


class SplitConfigFilter(filters.FilterSet):
    unnamed = filters.BooleanFilter(field_name="name", lookup_expr="isnull")

    class Meta:
        model = SplitConfig
        fields = ["name", "id", "unnamed"]


class SplitConfigViewSet(CreatedByResourceMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = SplitConfigSerializer
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = SplitConfigFilter
    pagination_class = StandardPagination

    def get_queryset(self):
        return (
            SplitConfig.objects.filter(self.get_owner_filter())
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


class EvaluationConfigViewSet(CreatedByResourceMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EvaluationConfigSerializer
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = EvaluationConfigFilter
    pagination_class = StandardPagination

    def get_queryset(self):
        return (
            EvaluationConfig.objects.filter(self.get_owner_filter())
            .select_related("created_by")
            .order_by("-ins_datetime")
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ParameterTuningJobViewSet(OwnedResourceMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ParameterTuningJobSerializer
    filterset_fields = ["id", "data__project", "data", "status"]
    pagination_class = StandardPagination
    owner_lookup = "data__project__owner"

    def get_queryset(self):
        return (
            ParameterTuningJob.objects.select_related(
                "data",
                "data__project",
                "split",
                "evaluation",
                "best_config",
                "tuned_model",
            )
            .prefetch_related("task_links")
            .filter(self.get_owner_filter())
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


class TaskLogViewSet(OwnedResourceMixin, viewsets.ReadOnlyModelViewSet):
    """Task logs filtered by ownership through the tuning job / model chain.

    Uses a custom owner filter because TaskLog connects to the project owner
    through two distinct FK paths (tuning_job_link and model_link).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = TaskLogSerializer
    filterset_class = TaskLogFilter
    pagination_class = StandardPagination

    def get_owner_filter(self) -> db_models.Q:
        user = self.request.user
        via_tuning = db_models.Q(task__tuning_job_link__isnull=False) & (
            db_models.Q(task__tuning_job_link__job__data__project__owner=user)
            | db_models.Q(task__tuning_job_link__job__data__project__owner__isnull=True)
        )
        via_model = db_models.Q(task__model_link__isnull=False) & (
            db_models.Q(task__model_link__model__data_loc__project__owner=user)
            | db_models.Q(
                task__model_link__model__data_loc__project__owner__isnull=True
            )
        )
        return via_tuning | via_model

    def get_queryset(self):
        return (
            TaskLog.objects.select_related("task")
            .filter(self.get_owner_filter())
            .distinct()
            .order_by("-id")
        )


class PingView(APIView):
    """Unauthenticated health-check endpoint for load balancer probes."""

    authentication_classes = []
    permission_classes = []

    @extend_schema(responses={200: PingSerializer})
    def get(self, request):
        try:
            _ = connections["default"]
            return Response(dict(success=True))
        except ConnectionDoesNotExist:
            raise APIException(detail=dict(success=False), code=400) from None


class ProjectSummaryView(APIView):
    @extend_schema(responses={200: ProjectSummarySerializer})
    def get(self, request, pk: int, format=None):
        project_obj = get_project_or_404(pk, user=request.user)
        summary = get_project_summary(project_obj)
        serializer = ProjectSummarySerializer(summary)
        return Response(serializer.data)
