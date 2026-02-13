"""Management command to create an API key from the command line."""

from datetime import timedelta
from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from recotem.api.authentication import generate_api_key
from recotem.api.models import ApiKey, Project

VALID_SCOPES = ["read", "write", "predict"]


class Command(BaseCommand):
    help = "Create an API key for a project (prints the raw key to stdout)"

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=int, required=True, help="Project ID")
        parser.add_argument(
            "--name", type=str, required=True, help="Name for the API key"
        )
        parser.add_argument(
            "--scopes",
            type=str,
            default="predict",
            help="Comma-separated scopes: read, write, predict (default: predict)",
        )
        parser.add_argument(
            "--expires-in-days",
            type=int,
            default=None,
            help="Number of days until the key expires (default: no expiry)",
        )
        parser.add_argument(
            "--owner",
            type=str,
            default="admin",
            help="Username of the key owner (default: admin)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Validate project
        try:
            project = Project.objects.get(id=options["project_id"])
        except Project.DoesNotExist:
            raise CommandError(
                f"Project with ID {options['project_id']} not found."
            ) from None

        # Validate owner
        try:
            owner = User.objects.get(username=options["owner"])
        except User.DoesNotExist:
            raise CommandError(f"User '{options['owner']}' not found.") from None

        # Validate scopes
        scopes = [s.strip() for s in options["scopes"].split(",") if s.strip()]
        for scope in scopes:
            if scope not in VALID_SCOPES:
                raise CommandError(
                    f"Invalid scope '{scope}'. Valid scopes: {VALID_SCOPES}"
                )
        if not scopes:
            raise CommandError("At least one scope is required.")

        # Validate owner matches project owner
        if project.owner_id is not None and project.owner_id != owner.id:
            raise CommandError(
                f"Owner '{owner.username}' does not match project owner "
                f"'{project.owner.username}'. API key owner must match the "
                f"project owner for management API access to work correctly."
            )

        # Check for duplicate name
        if ApiKey.objects.filter(project=project, name=options["name"]).exists():
            raise CommandError(
                f"API key with name '{options['name']}' already exists "
                f"for this project."
            )

        # Calculate expiry
        expires_at = None
        if options["expires_in_days"] is not None:
            expires_at = timezone.now() + timedelta(days=options["expires_in_days"])

        # Generate and create
        full_key, prefix, hashed_key = generate_api_key()
        ApiKey.objects.create(
            project=project,
            owner=owner,
            name=options["name"],
            key_prefix=prefix,
            hashed_key=hashed_key,
            scopes=scopes,
            expires_at=expires_at,
        )

        # Print the raw key — this is the only time it's visible
        self.stdout.write(full_key)
        self.stderr.write(
            self.style.SUCCESS(
                f"API key '{options['name']}' created for project '{project.name}' "
                f"(prefix: {prefix}..., scopes: {scopes})"
            )
        )
