from rest_framework.throttling import AnonRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    """Rate limit login attempts to prevent brute-force attacks."""

    scope = "login"
