import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
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

    @action(detail=False, methods=["post"])
    def batch(self, request):
        """Create multiple conversion events at once."""
        serializer = ConversionEventBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        events = serializer.save()
        return Response(
            {"created": len(events)},
            status=status.HTTP_201_CREATED,
        )
