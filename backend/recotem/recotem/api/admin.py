from django.contrib import admin

from recotem.api.models import (
    EvaluationConfig,
    ItemMetaData,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TaskLog,
    TrainedModel,
    TrainingData,
)

# Register your models here.

admin.site.register(Project)
admin.site.register(TrainingData)
admin.site.register(ItemMetaData)
admin.site.register(TrainedModel)
admin.site.register(ParameterTuningJob)
admin.site.register(SplitConfig)
admin.site.register(EvaluationConfig)
admin.site.register(TaskLog)
