"""
ASGI config for recotem project.

Supports both HTTP and WebSocket protocols via django-channels.
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "recotem.settings")

django_asgi_app = get_asgi_application()

from recotem.api.middleware import JwtAuthMiddleware  # noqa: E402
from recotem.api.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JwtAuthMiddleware(URLRouter(websocket_urlpatterns)),
    }
)
