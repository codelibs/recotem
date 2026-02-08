"""Assign an owner to Projects, SplitConfigs, and EvaluationConfigs that have none.

Usage:
    python manage.py assign_owners --user admin
    python manage.py assign_owners --user admin --dry-run
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from recotem.api.models import EvaluationConfig, Project, SplitConfig

User = get_user_model()


class Command(BaseCommand):
    help = "Assign owner/created_by to records that currently have none."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            required=True,
            help="Username to assign as owner/created_by.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without making modifications.",
        )

    def handle(self, *args, **options):
        username = options["user"]
        dry_run = options["dry_run"]

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"User '{username}' does not exist.") from None

        models_and_fields = [
            (Project, "owner"),
            (SplitConfig, "created_by"),
            (EvaluationConfig, "created_by"),
        ]

        for model, field_name in models_and_fields:
            qs = model.objects.filter(**{field_name: None})
            count = qs.count()
            label = model.__name__

            if count == 0:
                self.stdout.write(f"  {label}: no unowned records.")
                continue

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {label}: {count} record(s) "
                        f"would be assigned to '{username}'."
                    )
                )
            else:
                updated = qs.update(**{field_name: user})
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {label}: {updated} record(s) assigned to '{username}'."
                    )
                )

        if dry_run:
            self.stdout.write(self.style.NOTICE("\nDry run complete. No changes made."))
        else:
            self.stdout.write(self.style.SUCCESS("\nOwner assignment complete."))
