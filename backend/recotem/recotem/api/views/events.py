import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from recotem.api.authentication import ApiKeyAuthentication
from recotem.api.models import ConversionEvent
from recotem.api.serializers.events import (
    ConversionEventBatchSerializer,
    ConversionEventSerializer,
)
from recotem.api.views.mixins import OwnedResourceMixin
from recotem.api.views.pagination import StandardPagination

logger = logging.getLogger(__name__)


class HasPredictScopeOrIsAuthenticated(BasePermission):
    """Allow JWT-authenticated users or API keys with 'predict' scope."""

    def has_permission(self, request, view):
        api_key = getattr(request, "api_key", None)
        if api_key is not None:
            return "predict" in (api_key.scopes or [])
        return bool(request.user and request.user.is_authenticated)


class ConversionEventViewSet(OwnedResourceMixin, viewsets.ModelViewSet):
    authentication_classes = [
        ApiKeyAuthentication
    ] + viewsets.ModelViewSet.authentication_classes
    permission_classes = [HasPredictScopeOrIsAuthenticated]
    serializer_class = ConversionEventSerializer
    filterset_fields = ["project", "deployment_slot", "event_type"]
    pagination_class = StandardPagination
    owner_lookup = "project__owner"

    def get_queryset(self):
        return (
            ConversionEvent.objects.select_related("project", "deployment_slot")
            .filter(self.get_owner_filter())
            .order_by("-timestamp")
        )

    def _validate_event_access(self, project, deployment_slot):
        """Validate project access and deployment_slot consistency."""
        # API key: project must match the key's project
        api_key = getattr(self.request, "api_key", None)
        if api_key is not None:
            if project and project.id != api_key.project_id:
                raise PermissionDenied("API key not authorized for this project")
        else:
            # JWT user: must own the project (or project is unowned)
            user = self.request.user
            if project and project.owner_id is not None and project.owner_id != user.id:
                raise PermissionDenied("You do not own this project")

        # deployment_slot must belong to the specified project
        if project and deployment_slot and deployment_slot.project_id != project.id:
            raise PermissionDenied(
                "Deployment slot does not belong to the specified project"
            )

    def perform_create(self, serializer):
        data = serializer.validated_data
        self._validate_event_access(data.get("project"), data.get("deployment_slot"))
        serializer.save()

    @action(detail=False, methods=["post"])
    def batch(self, request):
        """Create multiple conversion events at once."""
        serializer = ConversionEventBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        for event_data in serializer.validated_data["events"]:
            self._validate_event_access(
                event_data.get("project"), event_data.get("deployment_slot")
            )
        events = serializer.save()
        return Response(
            {"created": len(events)},
            status=status.HTTP_201_CREATED,
        )
