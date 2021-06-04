from rest_framework.routers import DefaultRouter

from recotem.api.views import (
    EvaluationConfigViewSet,
    GetMeViewset,
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
router.register(r"getme", GetMeViewset, basename="getme")
router.register(r"project", ProjectViewSet, basename="project")
router.register(r"project_summary", ProjectSummaryViewSet, basename="project_summary")
router.register(r"training_data", TrainingDataViewset, basename="training_data")
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


from recotem.api.view_utils.tuning_job import (
    TrainingDataDetailViewset,
    TuningJobSummaryViewset,
)

router.register(
    "tuning_log_summary", TuningJobSummaryViewset, basename="tuning-log-summary"
)

router.register("data_detail", TrainingDataDetailViewset, basename="data-detail")
