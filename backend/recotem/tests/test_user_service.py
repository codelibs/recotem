"""Unit tests for user_service.py — user creation, activation,
and password management."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from recotem.api.services.user_service import (
    activate_user,
    admin_reset_password,
    create_user,
    deactivate_user,
)

User = get_user_model()


@pytest.mark.django_db
class TestCreateUser:
    def test_creates_user(self):
        """User created with hashed password (not stored as plaintext)."""
        user = create_user(username="newuser", password="SecurePass123!")
        assert user.pk is not None
        assert user.username == "newuser"
        assert user.check_password("SecurePass123!")
        # Password should be hashed, not stored as plaintext
        assert user.password != "SecurePass123!"

    def test_weak_password_raises(self):
        """Django validators reject weak password."""
        with pytest.raises(ValidationError):
            create_user(username="weakuser", password="123")

    def test_staff_flag(self):
        """is_staff=True propagated to created user."""
        user = create_user(
            username="staffuser", password="StaffPass123!", is_staff=True
        )
        assert user.is_staff is True


@pytest.mark.django_db
class TestDeactivateUser:
    def test_sets_inactive(self, db):
        """is_active set to False after deactivation."""
        user = User.objects.create_user(username="active", password="ActivePass123!")
        assert user.is_active is True

        result = deactivate_user(user)

        assert result.is_active is False
        # Verify persisted to database
        user.refresh_from_db()
        assert user.is_active is False


@pytest.mark.django_db
class TestActivateUser:
    def test_sets_active(self, db):
        """is_active set to True after activation."""
        user = User.objects.create_user(
            username="inactive", password="InactivePass123!"
        )
        user.is_active = False
        user.save(update_fields=["is_active"])

        result = activate_user(user)

        assert result.is_active is True
        # Verify persisted to database
        user.refresh_from_db()
        assert user.is_active is True


@pytest.mark.django_db
class TestAdminResetPassword:
    def test_password_changed(self, db):
        """New password verifiable after reset."""
        user = User.objects.create_user(
            username="resetuser", password="OldPassword123!"
        )
        assert user.check_password("OldPassword123!")

        admin_reset_password(user, "NewPassword456!")

        # New password works
        user.refresh_from_db()
        assert user.check_password("NewPassword456!")
        # Old password no longer works
        assert not user.check_password("OldPassword123!")

    def test_weak_password_rejected(self, db):
        """Raises ValidationError for weak new password."""
        user = User.objects.create_user(
            username="resetuser2", password="StrongPassword123!"
        )

        with pytest.raises(ValidationError):
            admin_reset_password(user, "123")
