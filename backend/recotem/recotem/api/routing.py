from django.urls import re_path

from recotem.api.consumers import JobStatusConsumer, TaskLogConsumer

websocket_urlpatterns = [
    re_path(r"^ws/job/(?P<job_id>\d+)/status/$", JobStatusConsumer.as_asgi()),
    re_path(r"^ws/job/(?P<job_id>\d+)/logs/$", TaskLogConsumer.as_asgi()),
]
