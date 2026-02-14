"""Serializers for user management endpoints."""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


class UserListSerializer(serializers.ModelSerializer):
    """Read-only serializer for user listing."""

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "is_staff",
            "is_active",
            "date_joined",
            "last_login",
        ]
        read_only_fields = fields


class UserCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a new user."""

    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "password", "is_staff"]
        read_only_fields = ["id"]

    def validate_password(self, value):
        validate_password(value)
        return value


class UserUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating an existing user."""

    class Meta:
        model = User
        fields = ["id", "username", "email", "is_staff", "is_active"]
        read_only_fields = ["id", "username"]


class AdminPasswordResetSerializer(serializers.Serializer):
    """Serializer for admin password reset."""

    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        user = self.context.get("user")
        validate_password(value, user)
        return value


class SelfPasswordChangeSerializer(serializers.Serializer):
    """Serializer for self-service password change."""

    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        user = self.context["request"].user
        validate_password(value, user)
        return value
