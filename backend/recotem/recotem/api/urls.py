from rest_framework.routers import DefaultRouter
from .views import (
    ProjectViewSet,
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
