from dj_rest_auth.views import LoginView
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.permissions import AllowAny
from rest_framework.routers import DefaultRouter

from recotem.api.throttles import LoginRateThrottle
from recotem.api.views import (
    EvaluationConfigViewSet,
    ItemMetaDataViewset,
    ModelConfigurationViewset,
    ParameterTuningJobViewSet,
    PingView,
    ProjectSummaryView,
    ProjectViewSet,
    SplitConfigViewSet,
    TaskLogViewSet,
    TrainedModelViewset,
    TrainingDataViewset,
    UserViewSet,
)
from recotem.api.views.ab_test import ABTestViewSet
from recotem.api.views.api_key import ApiKeyViewSet
from recotem.api.views.deployment import DeploymentSlotViewSet
from recotem.api.views.events import ConversionEventViewSet
from recotem.api.views.retraining import RetrainingRunViewSet, RetrainingScheduleViewSet

router = DefaultRouter()
router.register(r"project", ProjectViewSet, basename="project")
router.register(r"training_data", TrainingDataViewset, basename="training_data")
router.register(r"item_meta_data", ItemMetaDataViewset, basename="item_meta_data")
router.register(r"split_config", SplitConfigViewSet, basename="split_config")
router.register(
    r"evaluation_config", EvaluationConfigViewSet, basename="evaluation_config"
)
router.register(
    r"parameter_tuning_job", ParameterTuningJobViewSet, basename="parameter_tuning_job"
)
router.register(
    r"model_configuration", ModelConfigurationViewset, basename="model_configuration"
)
router.register(r"trained_model", TrainedModelViewset, basename="trained_model")
router.register(r"task_log", TaskLogViewSet, basename="task_log")
router.register(r"api_keys", ApiKeyViewSet, basename="api_key")
router.register(
    r"retraining_schedule", RetrainingScheduleViewSet, basename="retraining_schedule"
)
router.register(r"retraining_run", RetrainingRunViewSet, basename="retraining_run")
router.register(r"deployment_slot", DeploymentSlotViewSet, basename="deployment_slot")
router.register(r"ab_test", ABTestViewSet, basename="ab_test")
router.register(
    r"conversion_event", ConversionEventViewSet, basename="conversion_event"
)
router.register(r"users", UserViewSet, basename="user")


urlpatterns = [
    path("", include(router.urls)),
    path("ping/", PingView.as_view(), name="ping"),
    path("project_summary/<int:pk>/", ProjectSummaryView.as_view()),
    path(
        "auth/login/",
        LoginView.as_view(throttle_classes=[LoginRateThrottle]),
        name="rest_login",
    ),
    path("auth/", include("dj_rest_auth.urls")),
    # OpenAPI schema endpoints — public for API discoverability
    path(
        "schema/",
        SpectacularAPIView.as_view(permission_classes=[AllowAny]),
        name="schema",
    ),
    path(
        "schema/swagger-ui/",
        SpectacularSwaggerView.as_view(
            url_name="schema", permission_classes=[AllowAny]
        ),
        name="swagger-ui",
    ),
    path(
        "schema/redoc/",
        SpectacularRedocView.as_view(url_name="schema", permission_classes=[AllowAny]),
        name="redoc",
    ),
]
