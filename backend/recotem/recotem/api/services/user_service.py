"""User management business logic."""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

User = get_user_model()


def create_user(
    username: str,
    password: str,
    email: str = "",
    is_staff: bool = False,
) -> User:
    """Create a new user with validated password."""
    user = User(username=username, email=email, is_staff=is_staff)
    validate_password(password, user)
    user = User.objects.create_user(
        username=username,
        password=password,
        email=email,
        is_staff=is_staff,
    )
    return user


def deactivate_user(user: User) -> User:
    """Deactivate a user account (soft-delete)."""
    user.is_active = False
    user.save(update_fields=["is_active"])
    return user


def activate_user(user: User) -> User:
    """Re-activate a user account."""
    user.is_active = True
    user.save(update_fields=["is_active"])
    return user


def admin_reset_password(user: User, new_password: str) -> None:
    """Reset a user's password (admin action)."""
    validate_password(new_password, user)
    user.set_password(new_password)
    user.save(update_fields=["password"])
