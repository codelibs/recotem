import time

from django.db import connections
from django.db.utils import ConnectionDoesNotExist
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User


class Command(BaseCommand):
    r"""Wait for the db to start & create a superuser."""

    def handle(self, *args, **kwargs):
        self.stdout.write("Wait for the database to start...")
        conn = None
        while conn is None:
            try:
                conn = connections["default"]
            except ConnectionDoesNotExist:
                self.stdout.write("Connection not found. Wait for 2 second...")
                time.sleep(2)
        self.stdout.write(self.style.SUCCESS("Found connection."))
        n_users = User.objects.count()
        if n_users > 0:
            return
        self.stdout.write(
            'No user found. Create an administrative user with password "very_bad_password".'
        )
        User.objects.create(username="admin", password="very_bad_password")
