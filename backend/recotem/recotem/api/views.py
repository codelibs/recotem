from .models import (
    ParameterTuningLog,
    Project,
    TrainingData,
    SplitConfig,
    EvaluationConfig,
    ParameterTuningJob,
    TrainedModel,
)
from .serializers import (
    ParameterTuningJobSerializer,
    ParameterTuningLogSerializer,
    ProjectSerializer,
    TrainedModelSerializer,
    TrainingDataSerializer,
    SplitConfigSerializer,
    EvaluationConfigSerializer,
)
from rest_framework import viewsets


class TrainedModelViewset(viewsets.ModelViewSet):
    queryset = TrainedModel.objects.all()
    serializer_class = TrainedModelSerializer


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


class ParameterTuningLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ParameterTuningLog.objects.all()
    serializer_class = ParameterTuningLogSerializer
