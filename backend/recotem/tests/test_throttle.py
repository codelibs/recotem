"""Tests for rate limiting / throttling.

These tests verify that DRF throttle classes are applied correctly.
We test via PingView which has no authentication requirement.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from rest_framework.authentication import SessionAuthentication
from rest_framework.throttling import SimpleRateThrottle

from recotem.api.views import PingView

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """Clear cache before each throttle test to avoid cross-test interference."""
    cache.clear()
    yield
    cache.clear()


class _TestAnonThrottle(SimpleRateThrottle):
    """Anon throttle with a very low rate for testing."""

    scope = "test_anon"
    rate = "2/min"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class _TestUserThrottle(SimpleRateThrottle):
    """User throttle with a very low rate for testing."""

    scope = "test_user"
    rate = "3/min"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {
                "scope": self.scope,
                "ident": request.user.pk,
            }
        return None


@pytest.mark.django_db
def test_anon_rate_limit_is_enforced(client: Client):
    """Anonymous requests should be throttled after limit is exceeded."""
    with patch.object(PingView, "throttle_classes", [_TestAnonThrottle]):
        for _ in range(2):
            res = client.get(reverse("ping"))
            assert res.status_code == 200

        res = client.get(reverse("ping"))
        assert res.status_code == 429


@pytest.mark.django_db
def test_user_rate_limit_is_enforced(client: Client):
    """Authenticated user requests should be throttled after limit is exceeded."""
    user = User.objects.create_user(username="throttle_user", password="pass")
    client.force_login(user)

    with (
        patch.object(PingView, "throttle_classes", [_TestUserThrottle]),
        patch.object(PingView, "authentication_classes", [SessionAuthentication]),
    ):
        for _ in range(3):
            res = client.get(reverse("ping"))
            assert res.status_code == 200

        res = client.get(reverse("ping"))
        assert res.status_code == 429


@pytest.mark.django_db
def test_throttle_returns_retry_after_header(client: Client):
    """Throttled response should include Retry-After header."""
    with patch.object(PingView, "throttle_classes", [_TestAnonThrottle]):
        for _ in range(2):
            res = client.get(reverse("ping"))
            assert res.status_code == 200

        res = client.get(reverse("ping"))
        assert res.status_code == 429
        assert "Retry-After" in res


def test_recommendation_scoped_rate_limit():
    """Recommendation endpoints should use the 'recommendation' scoped throttle."""
    from rest_framework.throttling import ScopedRateThrottle

    from recotem.api.views.model import TrainedModelViewset

    # Verify the viewset returns ScopedRateThrottle for recommendation actions
    viewset = TrainedModelViewset()
    viewset.action = "recommendation"
    throttles = viewset.get_throttles()
    assert len(throttles) == 1
    assert isinstance(throttles[0], ScopedRateThrottle)
    assert viewset.throttle_scope == "recommendation"


@pytest.mark.django_db
def test_default_settings_include_throttle_classes():
    """Verify that the default REST_FRAMEWORK settings include throttle classes."""
    from django.conf import settings

    rf = settings.REST_FRAMEWORK
    assert "DEFAULT_THROTTLE_CLASSES" in rf
    assert len(rf["DEFAULT_THROTTLE_CLASSES"]) >= 1
    assert "DEFAULT_THROTTLE_RATES" in rf
    assert "anon" in rf["DEFAULT_THROTTLE_RATES"]
    assert "user" in rf["DEFAULT_THROTTLE_RATES"]
    assert "recommendation" in rf["DEFAULT_THROTTLE_RATES"]
