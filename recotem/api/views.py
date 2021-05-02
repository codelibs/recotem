from .models import Project, TrainingData
from .serializers import ProjectSerializer, TrainingDataSerializer
from rest_framework import viewsets


class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer


class TrainingDataViewset(viewsets.ModelViewSet):
    queryset = TrainingData.objects.all()
    serializer_class = TrainingDataSerializer


class ProjectDetailViewset(viewsets.ReadOnlyModelViewSet):
    queryset = Project
