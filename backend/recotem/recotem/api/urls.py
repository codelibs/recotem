from dj_rest_auth.views import LoginView
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.authtoken.views import obtain_auth_token
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
)

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


urlpatterns = [
    path("", include(router.urls)),
    path("ping/", PingView.as_view()),
    path("token/", obtain_auth_token),
    path("project_summary/<int:pk>/", ProjectSummaryView.as_view()),
    path(
        "auth/login/",
        LoginView.as_view(throttle_classes=[LoginRateThrottle]),
        name="rest_login",
    ),
    path("auth/", include("dj_rest_auth.urls")),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "schema/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"
    ),
]
