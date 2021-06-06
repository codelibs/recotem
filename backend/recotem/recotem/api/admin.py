from django.contrib import admin

from recotem.api.models import (
    EvaluationConfig,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TrainedModel,
    TrainingData,
)

# Register your models here.

admin.site.register(Project)
admin.site.register(SplitConfig)
