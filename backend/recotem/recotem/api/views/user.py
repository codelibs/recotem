"""User management ViewSet for admin operations and self-service password change."""

from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from recotem.api.authentication import DenyApiKeyAccess
from recotem.api.serializers.user import (
    AdminPasswordResetSerializer,
    SelfPasswordChangeSerializer,
    UserCreateSerializer,
    UserListSerializer,
    UserUpdateSerializer,
)
from recotem.api.services.user_service import (
    activate_user,
    admin_reset_password,
    create_user,
    deactivate_user,
)

User = get_user_model()


class UserViewSet(viewsets.ModelViewSet):
    """Admin user management and self-service password change."""

    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return User.objects.all().order_by("-date_joined")

    def get_permissions(self):
        if self.action == "change_password":
            return [IsAuthenticated(), DenyApiKeyAccess()]
        return [IsAuthenticated(), DenyApiKeyAccess(), IsAdminUser()]

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in ("partial_update", "update"):
            return UserUpdateSerializer
        if self.action == "reset_password":
            return AdminPasswordResetSerializer
        if self.action == "change_password":
            return SelfPasswordChangeSerializer
        return UserListSerializer

    def perform_create(self, serializer):
        data = serializer.validated_data
        user = create_user(
            username=data["username"],
            password=data["password"],
            email=data.get("email", ""),
            is_staff=data.get("is_staff", False),
        )
        serializer.instance = user

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        output = UserListSerializer(serializer.instance)
        return Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        """Deactivate a user (soft-delete)."""
        user = self.get_object()
        if user.pk == request.user.pk:
            return Response(
                {"detail": "You cannot deactivate your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deactivate_user(user)
        return Response(UserListSerializer(user).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """Re-activate a user."""
        user = self.get_object()
        activate_user(user)
        return Response(UserListSerializer(user).data)

    @action(detail=True, methods=["post"], url_path="reset_password")
    def reset_password(self, request, pk=None):
        """Admin: reset another user's password."""
        user = self.get_object()
        serializer = self.get_serializer(
            data=request.data, context={"request": request, "user": user}
        )
        serializer.is_valid(raise_exception=True)
        admin_reset_password(user, serializer.validated_data["new_password"])
        return Response({"detail": "Password has been reset."})

    @action(detail=False, methods=["post"], url_path="change_password")
    def change_password(self, request):
        """Self-service: change own password."""
        serializer = self.get_serializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        return Response({"detail": "Password changed successfully."})
