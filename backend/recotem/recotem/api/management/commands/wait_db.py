import time
from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import ConnectionDoesNotExist


class Command(BaseCommand):
    r"""Wait for the db to start & create a superuser."""

    def handle(self, *args: Any, **kwargs: Any) -> None:
        self.stdout.write("Wait for the database to start...")
        conn = None
        while conn is None:
            try:
                conn = connections["default"]
            except ConnectionDoesNotExist:
                self.stdout.write("Connection not found. Wait for 2 second...")
                time.sleep(2)
        self.stdout.write(self.style.SUCCESS("Found connection."))
