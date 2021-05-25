from django.contrib import admin
from django.urls import include, path
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .api.urls import router as api_router


class TopPageView(TemplateView):
    template_name = "index.html"


index_view = never_cache(TopPageView.as_view())

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", index_view, name="index"),
    # path("api/token-auth/", drf_views.obtain_auth_token, name="api-token"),
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/", include(api_router.urls)),
]
