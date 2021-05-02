from rest_framework.routers import DefaultRouter
from .views import ProjectViewSet, TrainingDataViewset

router = DefaultRouter()
router.register(r"project", ProjectViewSet, basename="project")
router.register(r"training_data", TrainingDataViewset, basename="training_data")
