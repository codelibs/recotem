from dj_rest_auth import serializers as dj_serializers
from django.contrib.auth import get_user_model

# Get the UserModel
UserModel = get_user_model()


class UserDetailsSerializer(dj_serializers.UserDetailsSerializer):
    """
    Custom User model w/o password
    """

    class Meta:
        extra_fields = []
        if hasattr(UserModel, "USERNAME_FIELD"):
            extra_fields.append(UserModel.USERNAME_FIELD)
        if hasattr(UserModel, "EMAIL_FIELD"):
            extra_fields.append(UserModel.EMAIL_FIELD)
        if hasattr(UserModel, "first_name"):
            extra_fields.append("first_name")
        if hasattr(UserModel, "last_name"):
            extra_fields.append("last_name")
        if hasattr(UserModel, "is_superuser"):
            extra_fields.append("is_superuser")
        model = UserModel
        fields = ("pk", *extra_fields)
        read_only_fields = ("email", "is_superuser")
