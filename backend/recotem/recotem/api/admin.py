from django.contrib import admin
from .models import (
    Project,
    ParameterTuningJob,
    TrainingData,
    TrainedModel,
    EvaluationConfig,
    SplitConfig,
)

# Register your models here.

admin.site.register(Project)
