from rest_framework import mixins, permissions, viewsets

from recotem.api.models import ParameterTuningJob, TrainingData
from recotem.api.serializers import ParameterTuningJobSerializer


class TuningJobSummaryViewset(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    queryset = ParameterTuningJob.objects.all()
    serializer_class = ParameterTuningJobSerializer
