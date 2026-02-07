from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("recotem.api.urls")),
    # DEPRECATED: Unversioned API endpoint for backward compatibility.
    # Clients should migrate to /api/v1/. This route will be removed in a future release.
    path("api/", include("recotem.api.urls")),
]
