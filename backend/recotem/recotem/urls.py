from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("recotem.api.urls")),
    # DEPRECATED: Unversioned API for backward compat.
    # Clients should migrate to /api/v1/.
    path("api/", include("recotem.api.urls")),
]
