from rest_framework import permissions, viewsets
from rest_framework.exceptions import PermissionDenied

from recotem.api.authentication import RequireManagementScope
from recotem.api.models import Project
from recotem.api.serializers.project import ProjectSerializer

from .mixins import OwnedResourceMixin


class ProjectViewSet(OwnedResourceMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, RequireManagementScope]
    serializer_class = ProjectSerializer
    filterset_fields = ["id", "name"]
    owner_lookup = "owner"

    def get_queryset(self):
        return Project.objects.filter(self.get_owner_filter())

    def perform_create(self, serializer):
        """Auto-set the owner to the requesting user."""
        api_key = getattr(self.request, "api_key", None)
        if api_key is not None:
            raise PermissionDenied("API keys cannot create new projects.")
        serializer.save(owner=self.request.user)
