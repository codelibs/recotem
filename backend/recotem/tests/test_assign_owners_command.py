"""Tests for the assign_owners management command."""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from recotem.api.models import EvaluationConfig, Project, SplitConfig

User = get_user_model()


@pytest.fixture
def target_user(db):
    return User.objects.create_user(username="assign_target", password="pass")


@pytest.mark.django_db
class TestAssignOwnersCommand:
    def test_assigns_owner_to_unowned_projects(self, target_user):
        p = Project.objects.create(
            name="unowned", user_column="u", item_column="i", owner=None
        )
        out = StringIO()
        call_command("assign_owners", "--user", target_user.username, stdout=out)

        p.refresh_from_db()
        assert p.owner == target_user
        assert "assigned" in out.getvalue()

    def test_assigns_created_by_to_configs(self, target_user):
        sc = SplitConfig.objects.create(created_by=None)
        ec = EvaluationConfig.objects.create(created_by=None)

        out = StringIO()
        call_command("assign_owners", "--user", target_user.username, stdout=out)

        sc.refresh_from_db()
        ec.refresh_from_db()
        assert sc.created_by == target_user
        assert ec.created_by == target_user

    def test_dry_run_no_changes(self, target_user):
        p = Project.objects.create(
            name="dry_run_proj", user_column="u", item_column="i", owner=None
        )
        sc = SplitConfig.objects.create(created_by=None)

        out = StringIO()
        call_command(
            "assign_owners", "--user", target_user.username, "--dry-run", stdout=out
        )

        p.refresh_from_db()
        sc.refresh_from_db()
        assert p.owner is None
        assert sc.created_by is None
        assert "Dry run complete" in out.getvalue()

    def test_invalid_user_raises(self):
        with pytest.raises(CommandError, match="does not exist"):
            call_command("assign_owners", "--user", "ghost_user")

    def test_no_unowned_records(self, target_user):
        # Create records that already have owners
        Project.objects.create(
            name="owned_proj",
            user_column="u",
            item_column="i",
            owner=target_user,
        )
        SplitConfig.objects.create(created_by=target_user)
        EvaluationConfig.objects.create(created_by=target_user)

        out = StringIO()
        call_command("assign_owners", "--user", target_user.username, stdout=out)

        assert "no unowned records" in out.getvalue()
