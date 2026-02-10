import time
from typing import Any

from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    r"""Wait for the database to become available."""

    def handle(self, *args: Any, **kwargs: Any) -> None:
        self.stdout.write("Waiting for database...")
        max_retries = 30
        for attempt in range(1, max_retries + 1):
            try:
                connection = connections["default"]
                connection.ensure_connection()
                self.stdout.write(self.style.SUCCESS("Database available."))
                return
            except OperationalError:
                self.stdout.write(
                    f"Database unavailable (attempt {attempt}/{max_retries}), "
                    "waiting 2 seconds..."
                )
                time.sleep(2)
        self.stderr.write(self.style.ERROR("Database unavailable after max retries."))
        raise SystemExit(1)
