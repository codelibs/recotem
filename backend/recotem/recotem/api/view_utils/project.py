from rest_framework import mixins, permissions, viewsets

from recotem.api.models import Project
from recotem.api.serializer_utils.project import (
    ProjectSerializer,
    ProjectSummarySerializer,
)


class ProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    filterset_fields = ["id", "name"]


class ProjectSummaryViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    queryset = Project.objects.all()
    serializer_class = ProjectSummarySerializer