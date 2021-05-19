from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    r"""Wait for the db to start & create a superuser."""

    def handle(self, *args, **kwargs):
        n_users = User.objects.count()
        if n_users > 0:
            return
        self.stdout.write(
            "No user found. "
            'Create an administrative user with username "admin"'
            ' and password "very_bad_password".'
        )
        User.objects.create_superuser(username="admin", password="very_bad_password")
