"""Sign all existing unsigned trained model files with HMAC-SHA256.

After running this command, set PICKLE_ALLOW_LEGACY_UNSIGNED=false
to reject any unsigned model files.

Usage:
    python manage.py resign_models
    python manage.py resign_models --dry-run
"""

import logging

from django.core.management.base import BaseCommand

from recotem.api.models import TrainedModel
from recotem.api.services.pickle_signing import (
    HMAC_SIZE,
    _compute_hmac,
    sign_pickle_bytes,
)

logger = logging.getLogger(__name__)


def _is_signed(data: bytes) -> bool:
    """Check whether data already has a valid HMAC signature."""
    if len(data) <= HMAC_SIZE:
        return False
    signature = data[:HMAC_SIZE]
    payload = data[HMAC_SIZE:]
    import hmac

    return hmac.compare_digest(signature, _compute_hmac(payload))


class Command(BaseCommand):
    help = (
        "Sign all unsigned trained model files with HMAC-SHA256. "
        "After running, set PICKLE_ALLOW_LEGACY_UNSIGNED=false."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be signed without making modifications.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        models = TrainedModel.objects.exclude(file="")
        total = models.count()
        signed = 0
        already_signed = 0
        errors = 0

        self.stdout.write(f"Checking {total} trained model(s)...")

        for model in models.iterator():
            try:
                with model.file.open("rb") as f:
                    data = f.read()
            except (OSError, FileNotFoundError) as e:
                self.stderr.write(
                    self.style.ERROR(f"  Model {model.pk}: cannot read file — {e}")
                )
                errors += 1
                continue

            if _is_signed(data):
                already_signed += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Model {model.pk}: would be signed ({len(data)} bytes)"
                    )
                )
                signed += 1
                continue

            signed_data = sign_pickle_bytes(data)
            try:
                with model.file.open("wb") as f:
                    f.write(signed_data)
                signed += 1
                self.stdout.write(
                    self.style.SUCCESS(f"  Model {model.pk}: signed ({len(data)} bytes)")
                )
            except OSError as e:
                self.stderr.write(
                    self.style.ERROR(f"  Model {model.pk}: write failed — {e}")
                )
                errors += 1

        self.stdout.write("")
        self.stdout.write(f"  Already signed: {already_signed}")
        self.stdout.write(f"  Newly signed:   {signed}")
        if errors:
            self.stdout.write(self.style.ERROR(f"  Errors:         {errors}"))

        if dry_run:
            self.stdout.write(self.style.NOTICE("\nDry run complete. No changes made."))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nDone. You can now set PICKLE_ALLOW_LEGACY_UNSIGNED=false."
                )
            )
