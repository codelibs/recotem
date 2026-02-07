import pytest
from django.test import Client, override_settings
from django.urls import reverse


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
        assert res.status_code in (200, 401)

    res = client.get(reverse("project-list"))
    assert res.status_code == 429
