import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from recotem.api.authentication import RequireManagementScope, generate_api_key
from recotem.api.models import ApiKey
from recotem.api.serializers.api_key import ApiKeyCreateSerializer, ApiKeySerializer
from recotem.api.views.mixins import OwnedResourceMixin
from recotem.api.views.pagination import StandardPagination

logger = logging.getLogger(__name__)


class ApiKeyViewSet(OwnedResourceMixin, viewsets.ModelViewSet):
    """Manage API keys for project access."""

    permission_classes = [IsAuthenticated, RequireManagementScope]
    filterset_fields = ["project"]
    pagination_class = StandardPagination
    owner_lookup = "project__owner"

    def get_serializer_class(self):
        if self.action == "create":
            return ApiKeyCreateSerializer
        return ApiKeySerializer

    def get_queryset(self):
        return (
            ApiKey.objects.select_related("project", "owner")
            .filter(self.get_owner_filter())
            .order_by("-ins_datetime")
        )

    def perform_create(self, serializer):
        full_key, prefix, hashed_key = generate_api_key()
        instance = serializer.save(
            owner=self.request.user,
            key_prefix=prefix,
            hashed_key=hashed_key,
        )
        # Attach the full key to the instance for the response
        instance._full_key = full_key

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        instance = serializer.instance
        data = ApiKeyCreateSerializer(instance, context={"request": request}).data
        data["key"] = instance._full_key
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """Revoke (deactivate) an API key."""
        key_obj = self.get_object()
        key_obj.is_active = False
        key_obj.save(update_fields=["is_active", "updated_at"])
        return Response({"status": "revoked"})

    def destroy(self, request, *args, **kwargs):
        """Delete an API key permanently."""
        return super().destroy(request, *args, **kwargs)
