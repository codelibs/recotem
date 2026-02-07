import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import reverse

User = get_user_model()


@pytest.mark.django_db
@override_settings(
    REST_FRAMEWORK={
        "DEFAULT_AUTHENTICATION_CLASSES": [
            "rest_framework.authentication.SessionAuthentication",
        ],
        "DEFAULT_PERMISSION_CLASSES": [],
        "DEFAULT_THROTTLE_CLASSES": [
            "rest_framework.throttling.AnonRateThrottle",
        ],
        "DEFAULT_THROTTLE_RATES": {
            "anon": "2/min",
        },
    }
)
def test_anon_rate_limit_is_enforced(client: Client):
    """Anonymous requests should be throttled after limit is exceeded."""
    for _ in range(2):
        res = client.get(reverse("project-list"))
        # ViewSet may return 401 or 403 for unauthenticated users
        assert res.status_code in (200, 401, 403)

    res = client.get(reverse("project-list"))
    assert res.status_code == 429


@pytest.mark.django_db
@override_settings(
    REST_FRAMEWORK={
        "DEFAULT_AUTHENTICATION_CLASSES": [
            "rest_framework.authentication.SessionAuthentication",
        ],
        "DEFAULT_PERMISSION_CLASSES": [
            "rest_framework.permissions.IsAuthenticated",
        ],
        "DEFAULT_THROTTLE_CLASSES": [
            "rest_framework.throttling.UserRateThrottle",
        ],
        "DEFAULT_THROTTLE_RATES": {
            "user": "3/min",
        },
    }
)
def test_user_rate_limit_is_enforced(client: Client):
    """Authenticated user requests should be throttled after limit is exceeded."""
    user = User.objects.create_user(username="throttle_user", password="pass")
    client.force_login(user)

    for _ in range(3):
        res = client.get(reverse("project-list"))
        assert res.status_code == 200

    res = client.get(reverse("project-list"))
    assert res.status_code == 429


@pytest.mark.django_db
@override_settings(
    REST_FRAMEWORK={
        "DEFAULT_AUTHENTICATION_CLASSES": [
            "rest_framework.authentication.SessionAuthentication",
        ],
        "DEFAULT_PERMISSION_CLASSES": [],
        "DEFAULT_THROTTLE_CLASSES": [
            "rest_framework.throttling.AnonRateThrottle",
        ],
        "DEFAULT_THROTTLE_RATES": {
            "anon": "2/min",
        },
    }
)
def test_throttle_returns_retry_after_header(client: Client):
    """Throttled response should include Retry-After header."""
    for _ in range(2):
        res = client.get(reverse("project-list"))
        assert res.status_code in (200, 401, 403)

    res = client.get(reverse("project-list"))
    assert res.status_code == 429
    assert "Retry-After" in res
