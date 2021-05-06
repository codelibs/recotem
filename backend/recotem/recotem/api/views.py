from .models import (
    Project,
    TrainingData,
    SplitConfig,
    EvaluationConfig,
    ParameterTuningJob,
)
from .serializers import (
    ParameterTuningJobSerializer,
    ProjectSerializer,
    TrainingDataSerializer,
    SplitConfigSerializer,
    EvaluationConfigSerializer,
)
from rest_framework import viewsets


class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer


class TrainingDataViewset(viewsets.ModelViewSet):
    queryset = TrainingData.objects.all()
    serializer_class = TrainingDataSerializer


class SplitConfigViewSet(viewsets.ModelViewSet):
    queryset = SplitConfig.objects.all()
    serializer_class = SplitConfigSerializer


class EvaluationConfigViewSet(viewsets.ModelViewSet):
    queryset = EvaluationConfig.objects.all()
    serializer_class = EvaluationConfigSerializer


class ParameterTuningJobViewSet(viewsets.ModelViewSet):
    queryset = ParameterTuningJob.objects.all()
    serializer_class = ParameterTuningJobSerializer
