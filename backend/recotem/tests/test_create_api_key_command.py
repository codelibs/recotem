"""Tests for the create_api_key management command."""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from recotem.api.models import ApiKey, Project

User = get_user_model()


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(username="admin", password="pass")


@pytest.fixture
def project(admin_user):
    return Project.objects.create(
        name="test_project", user_column="user", item_column="item", owner=admin_user
    )


@pytest.mark.django_db
class TestCreateApiKeyCommand:
    def test_creates_key_prints_to_stdout(self, project, admin_user):
        out = StringIO()
        call_command(
            "create_api_key",
            "--project-id",
            str(project.id),
            "--name",
            "my-key",
            "--owner",
            admin_user.username,
            stdout=out,
        )
        raw_key = out.getvalue().strip()
        assert raw_key.startswith("rctm_")
        assert ApiKey.objects.filter(project=project, name="my-key").exists()

    def test_invalid_project_raises(self, admin_user):
        with pytest.raises(CommandError, match="not found"):
            call_command(
                "create_api_key",
                "--project-id",
                "99999",
                "--name",
                "bad-key",
                "--owner",
                admin_user.username,
            )

    def test_invalid_owner_raises(self, project):
        with pytest.raises(CommandError, match="not found"):
            call_command(
                "create_api_key",
                "--project-id",
                str(project.id),
                "--name",
                "bad-key",
                "--owner",
                "nonexistent_user",
            )

    def test_invalid_scope_raises(self, project, admin_user):
        with pytest.raises(CommandError, match="Invalid scope"):
            call_command(
                "create_api_key",
                "--project-id",
                str(project.id),
                "--name",
                "bad-scope-key",
                "--scopes",
                "predict,badscope",
                "--owner",
                admin_user.username,
            )

    def test_owner_project_mismatch_raises(self, project):
        other_user = User.objects.create_user(username="other", password="pass")
        with pytest.raises(CommandError, match="does not match project owner"):
            call_command(
                "create_api_key",
                "--project-id",
                str(project.id),
                "--name",
                "mismatch-key",
                "--owner",
                other_user.username,
            )

    def test_duplicate_name_raises(self, project, admin_user):
        call_command(
            "create_api_key",
            "--project-id",
            str(project.id),
            "--name",
            "dup-key",
            "--owner",
            admin_user.username,
            stdout=StringIO(),
        )
        with pytest.raises(CommandError, match="already exists"):
            call_command(
                "create_api_key",
                "--project-id",
                str(project.id),
                "--name",
                "dup-key",
                "--owner",
                admin_user.username,
            )

    def test_expires_in_days_sets_expiry(self, project, admin_user):
        before = timezone.now()
        call_command(
            "create_api_key",
            "--project-id",
            str(project.id),
            "--name",
            "expiring-key",
            "--expires-in-days",
            "30",
            "--owner",
            admin_user.username,
            stdout=StringIO(),
        )
        after = timezone.now()

        key = ApiKey.objects.get(project=project, name="expiring-key")
        assert key.expires_at is not None
        from datetime import timedelta

        assert (
            before + timedelta(days=30) <= key.expires_at <= after + timedelta(days=30)
        )
