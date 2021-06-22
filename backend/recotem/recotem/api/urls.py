from django.urls import include, path
from rest_framework.routers import DefaultRouter

from recotem.api.views import (
    EvaluationConfigViewSet,
    ItemMetaDataViewset,
    ModelConfigurationViewset,
    ParameterTuningJobViewSet,
    ProjectSummaryViewSet,
    ProjectViewSet,
    SplitConfigViewSet,
    TaskLogViewSet,
    TrainedModelViewset,
    TrainingDataViewset,
)

router = DefaultRouter()
router.register(r"project", ProjectViewSet, basename="project")
router.register(r"project_summary", ProjectSummaryViewSet, basename="project_summary")
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
    path("auth/", include("dj_rest_auth.urls")),
]
