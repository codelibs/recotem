"""API key authentication for DRF."""

import logging
import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

logger = logging.getLogger(__name__)

API_KEY_PREFIX = "rctm_"
API_KEY_RANDOM_LENGTH = 48  # Characters after prefix


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns (full_key, prefix, hashed_key).
    The full_key is shown to the user once; prefix and hashed_key are stored.
    """
    random_part = secrets.token_urlsafe(API_KEY_RANDOM_LENGTH)
    full_key = f"{API_KEY_PREFIX}{random_part}"
    prefix = random_part[:8]
    hashed_key = make_password(full_key)
    return full_key, prefix, hashed_key


class ApiKeyAuthentication(BaseAuthentication):
    """Authenticate requests using X-API-Key header."""

    keyword = "X-API-Key"

    def authenticate(self, request):
        api_key = request.META.get("HTTP_X_API_KEY")
        if not api_key:
            return None

        if not api_key.startswith(API_KEY_PREFIX):
            return None

        # Import here to avoid circular imports
        from recotem.api.models import ApiKey

        random_part = api_key[len(API_KEY_PREFIX) :]
        if len(random_part) < 8:
            raise AuthenticationFailed("Invalid API key format.")

        prefix = random_part[:8]

        try:
            key_obj = ApiKey.objects.select_related("owner", "project").get(
                key_prefix=prefix, is_active=True
            )
        except ApiKey.DoesNotExist:
            raise AuthenticationFailed("Invalid API key.") from None
        except ApiKey.MultipleObjectsReturned:
            raise AuthenticationFailed("Ambiguous API key prefix.") from None

        if key_obj.expires_at and key_obj.expires_at < timezone.now():
            raise AuthenticationFailed("API key has expired.")

        if not check_password(api_key, key_obj.hashed_key):
            raise AuthenticationFailed("Invalid API key.")

        # Update last_used_at (fire-and-forget, don't fail the request)
        try:
            ApiKey.objects.filter(pk=key_obj.pk).update(last_used_at=timezone.now())
        except Exception:
            logger.debug("Failed to update last_used_at for key %s", key_obj.pk)

        # Attach key info to request for scope checking
        request.api_key = key_obj
        return (key_obj.owner, key_obj)

    def authenticate_header(self, request):
        return self.keyword
