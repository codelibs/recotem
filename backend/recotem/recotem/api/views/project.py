from rest_framework import mixins, permissions, viewsets

from recotem.api.models import Project
from recotem.api.serializers.project import ProjectSerializer, ProjectSummarySerializer


class ProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    filterset_fields = ["id", "name"]
