from rest_framework import serializers


class PingSerializer(serializers.Serializer):
    success = serializers.BooleanField()
