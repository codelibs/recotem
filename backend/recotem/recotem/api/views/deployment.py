from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from recotem.api.authentication import RequireManagementScope
from recotem.api.models import DeploymentSlot
from recotem.api.serializers.deployment import DeploymentSlotSerializer
from recotem.api.views.mixins import OwnedResourceMixin
from recotem.api.views.pagination import StandardPagination


class DeploymentSlotViewSet(OwnedResourceMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, RequireManagementScope]
    serializer_class = DeploymentSlotSerializer
    filterset_fields = ["project", "is_active"]
    pagination_class = StandardPagination
    owner_lookup = "project__owner"

    def get_queryset(self):
        return (
            DeploymentSlot.objects.select_related("project", "trained_model")
            .filter(self.get_owner_filter())
            .order_by("-ins_datetime")
        )
