"""Tests for the create_test_users management command."""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

User = get_user_model()


@pytest.mark.django_db
class TestCreateTestUsersCommand:
    def test_creates_new_user(self):
        call_command("create_test_users", "--user", "newuser:secret123")

        user = User.objects.get(username="newuser")
        assert user.check_password("secret123")

    def test_updates_existing_user(self):
        User.objects.create_user(username="existing", password="oldpass")
        call_command("create_test_users", "--user", "existing:newpass")

        user = User.objects.get(username="existing")
        assert user.check_password("newpass")
        assert not user.check_password("oldpass")

    def test_invalid_format_raises(self):
        with pytest.raises(CommandError, match="Expected format"):
            call_command("create_test_users", "--user", "nocolonhere")

    def test_empty_username_raises(self):
        with pytest.raises(CommandError, match="Username and password are required"):
            call_command("create_test_users", "--user", ":password")
