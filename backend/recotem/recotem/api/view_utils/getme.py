from rest_framework import mixins, serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from ..models import User
from ..serializers import UserSerializer


class GetMeViewset(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer
    queryset = User.objects.all()

    def get_queryset(self):
        return User.objects.filter(id=self.request.user.id)
