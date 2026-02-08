from rest_framework import permissions, viewsets

from recotem.api.models import Project
from recotem.api.serializers.project import ProjectSerializer

from .mixins import OwnedResourceMixin


class ProjectViewSet(OwnedResourceMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProjectSerializer
    filterset_fields = ["id", "name"]
    owner_lookup = "owner"

    def get_queryset(self):
        return Project.objects.filter(self.get_owner_filter())

    def perform_create(self, serializer):
        """Auto-set the owner to the requesting user."""
        serializer.save(owner=self.request.user)
