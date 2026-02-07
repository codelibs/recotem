from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update test users. Use --user username:password (repeatable)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--user",
            action="append",
            dest="users",
            required=True,
            help="User credential pair in the form username:password",
        )

    def handle(self, *args: Any, **kwargs: Any) -> None:
        users: list[str] = kwargs["users"]
        User = get_user_model()

        for raw in users:
            if ":" not in raw:
                raise CommandError(
                    f"Invalid --user value '{raw}'. Expected format: username:password"
                )
            username, password = raw.split(":", 1)
            username = username.strip()
            if not username or not password:
                raise CommandError(
                    f"Invalid --user value '{raw}'. Username and password are required."
                )

            user, created = User.objects.get_or_create(username=username)
            user.set_password(password)
            user.save()
            self.stdout.write(
                f"{'Created' if created else 'Updated'} test user '{username}'."
            )
