import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from recotem.api.authentication import RequireManagementScope
from recotem.api.models import RetrainingRun, RetrainingSchedule
from recotem.api.serializers.retraining import (
    RetrainingRunSerializer,
    RetrainingScheduleSerializer,
)
from recotem.api.services.schedule_service import (
    delete_beat_task,
    sync_schedule_to_beat,
)
from recotem.api.views.mixins import OwnedResourceMixin
from recotem.api.views.pagination import StandardPagination

logger = logging.getLogger(__name__)


class RetrainingScheduleViewSet(OwnedResourceMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, RequireManagementScope]
    serializer_class = RetrainingScheduleSerializer
    filterset_fields = ["project"]
    pagination_class = StandardPagination
    owner_lookup = "project__owner"

    def get_queryset(self):
        return (
            RetrainingSchedule.objects.select_related("project")
            .filter(self.get_owner_filter())
            .order_by("-ins_datetime")
        )

    def perform_create(self, serializer):
        instance = serializer.save()
        sync_schedule_to_beat(instance)

    def perform_update(self, serializer):
        instance = serializer.save()
        sync_schedule_to_beat(instance)

    def perform_destroy(self, instance):
        delete_beat_task(instance)  # Always delete, don't sync
        instance.delete()

    @action(detail=True, methods=["post"])
    def trigger(self, request, pk=None):
        """Manually trigger a retraining run."""
        schedule = self.get_object()
        from recotem.api.tasks import task_scheduled_retrain

        task_scheduled_retrain.delay(schedule.id)
        return Response({"status": "triggered"}, status=status.HTTP_202_ACCEPTED)


class RetrainingRunViewSet(
    OwnedResourceMixin, ListModelMixin, RetrieveModelMixin, viewsets.GenericViewSet
):
    permission_classes = [IsAuthenticated, RequireManagementScope]
    serializer_class = RetrainingRunSerializer
    filterset_fields = ["schedule", "status"]
    pagination_class = StandardPagination
    owner_lookup = "schedule__project__owner"

    def get_queryset(self):
        return (
            RetrainingRun.objects.select_related("schedule", "schedule__project")
            .filter(self.get_owner_filter())
            .order_by("-ins_datetime")
        )
