from django.db import models
from rest_framework import permissions, viewsets

from recotem.api.models import Project
from recotem.api.serializers.project import ProjectSerializer, ProjectSummarySerializer


class ProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProjectSerializer
    filterset_fields = ["id", "name"]

    def get_queryset(self):
        """Return only projects owned by the current user, or unowned projects."""
        user = self.request.user
        return Project.objects.filter(
            models.Q(owner=user) | models.Q(owner__isnull=True)
        )

    def perform_create(self, serializer):
        """Auto-set the owner to the requesting user."""
        serializer.save(owner=self.request.user)
