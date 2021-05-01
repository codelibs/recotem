from django.shortcuts import render
from django.views.generic import TemplateView
from django.views.decorators.cache import never_cache


# Serve Vue Application
class TopPageView(TemplateView):
    template_name = "index.html"


index_view = never_cache(TopPageView.as_view())
# Create your views here.
