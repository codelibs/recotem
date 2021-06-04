from rest_framework import mixins, permissions, viewsets

from recotem.api.models import ParameterTuningJob, TrainingData
from recotem.api.serializer_utils import (
    ParameterTuningJobListSerializer,
    TrainingDataDetailSerializer,
)


class TuningJobSummaryViewset(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    queryset = ParameterTuningJob.objects.all()
    serializer_class = ParameterTuningJobListSerializer


class TrainingDataDetailViewset(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permissions_classes = [permissions.IsAuthenticated]
    queryset = TrainingData.objects.all()
    serializer_class = TrainingDataDetailSerializer
