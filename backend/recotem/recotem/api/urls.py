from rest_framework.routers import DefaultRouter
from .views import (
    ParameterTuningJobViewSet,
    ParameterTuningLogViewSet,
    ProjectViewSet,
    TrainedModelViewset,
    TrainingDataViewset,
    SplitConfigViewSet,
    EvaluationConfigViewSet,
)

router = DefaultRouter()
router.register(r"project", ProjectViewSet, basename="project")
router.register(r"training_data", TrainingDataViewset, basename="training_data")
router.register(r"split_config", SplitConfigViewSet, basename="split_config")
router.register(
    r"evaluation_config", EvaluationConfigViewSet, basename="evaluation_config"
)
router.register(
    r"parameter_tuning_job", ParameterTuningJobViewSet, basename="parameter_tuning_job"
)
router.register(r"trained_model", TrainedModelViewset, basename="trained_model")
router.register(
    r"parameter_tuning_log", ParameterTuningLogViewSet, basename="parameter_tuning_log"
)
