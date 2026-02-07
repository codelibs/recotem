from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("recotem.api.urls")),
    # Backward compatibility: unversioned API still works
    path("api/", include("recotem.api.urls")),
]
