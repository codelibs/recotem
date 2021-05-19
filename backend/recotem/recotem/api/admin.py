from django.contrib import admin

from .models import (
    EvaluationConfig,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TrainedModel,
    TrainingData,
)

# Register your models here.

admin.site.register(Project)
