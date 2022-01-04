import os
import secrets
import string
from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    r"""Wait for the db to start & create a superuser."""

    def handle(self, *args: Any, **kwargs: Any) -> None:
        n_users = User.objects.count()
        if n_users > 0:
            return
        pwd: str = os.environ.get("DEFAULT_ADMIN_PASSWORD")
        if pwd is None:
            pwd_chars = string.ascii_uppercase + string.ascii_lowercase + string.digits
            pwd = "".join(secrets.choice(pwd_chars) for _ in range(12))
            pwd_msg = f' and password "{pwd}"'
        else:
            pwd_msg = ""
        self.stdout.write(
            "No user found. "
            'Create an administrative user with username "admin"'
            f"{pwd_msg}."
        )
        User.objects.create_superuser(username="admin", password=pwd)
