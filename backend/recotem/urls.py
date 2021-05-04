"""recotem URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

from rest_framework.authtoken import views as drf_views

from django.views.generic import TemplateView
from django.views.decorators.cache import never_cache
from .api.urls import router as api_router


class TopPageView(TemplateView):
    template_name = "index.html"


index_view = never_cache(TopPageView.as_view())

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", index_view, name="index"),
    path("api/token-auth/", drf_views.obtain_auth_token, name="api-token"),
    path("api/", include(api_router.urls)),
]
