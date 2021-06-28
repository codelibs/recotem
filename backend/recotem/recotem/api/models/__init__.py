from pathlib import Path
from typing import Optional

import pandas as pd
from django.conf import settings
from django.db import models
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from django_celery_results.models import TaskResult
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError

from ..utils import read_dataframe
from .base_file_model import BaseFileModel


@receiver(models.signals.post_save, sender=settings.AUTH_USER_MODEL)
def create_auth_token(sender, instance=None, created=False, **kwargs):
    if created:
        Token.objects.create(user=instance)


class ModelWithInsDatetime(models.Model):
    ins_datetime = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class Project(ModelWithInsDatetime):
    name = models.TextField(unique=True)
    user_column = models.CharField(max_length=256)
    item_column = models.CharField(max_length=256)
    time_column = models.CharField(max_length=256, blank=False, null=True)


class TrainingData(ModelWithInsDatetime, BaseFileModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)

    def validate_return_df(self) -> pd.DataFrame:
        pathname = Path(self.file.name)
        df = read_dataframe(pathname, self.file)
        user_column: str = self.project.user_column
        item_column: str = self.project.item_column
        time_column: Optional[str] = self.project.time_column

        if time_column is not None:
            if time_column not in df:
                raise ValidationError(
                    f'Column "{time_column}" not found in the upload file.'
                )
            try:
                df[time_column] = pd.to_datetime(df[time_column])
            except ValueError:
                raise ValidationError(
                    f'Could not interpret "{time_column}" as datetime.'
                )

        if user_column not in df:
            raise ValidationError(
                f'Column "{user_column}" not found in the upload file.'
            )
        if item_column not in df:
            raise ValidationError(
                f'Column "{item_column}" not found in the upload file.'
            )
        return df


class ItemMetaData(ModelWithInsDatetime, BaseFileModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    valid_columns_list_json = models.TextField(null=True)

    def validate_return_df(self) -> pd.DataFrame:
        pathname = Path(self.file.name)
        df = read_dataframe(pathname, self.file)
        item_column: str = self.project.item_column
        if item_column not in df:
            raise ValidationError(
                f'Column "{item_column}" not found in the upload file.'
            )
        df[item_column] = [str(id) for id in df[item_column]]
        return df


@receiver(models.signals.post_save, sender=TrainingData)
def save_file_size(
    sender, instance: Optional[TrainingData] = None, created: bool = False, **kwargs
) -> None:
    if not created:
        return
    if not bool(instance.file):
        raise ValidationError(detail="file is required.")
    instance.filesize = instance.file.size
    instance.save()


class SplitConfig(ModelWithInsDatetime):
    name = models.CharField(max_length=256, null=True)

    class SplitScheme(models.TextChoices):
        RANDOM = "RG", _("Random")
        TIME_GLOBAL = "TG", _("Time Global")
        TIME_USER = "TU", _("Time User")

    scheme = models.CharField(
        choices=SplitScheme.choices, max_length=2, default=SplitScheme.RANDOM
    )
    heldout_ratio = models.FloatField(default=0.1)
    n_heldout = models.IntegerField(null=True)

    test_user_ratio = models.FloatField(default=1.0)
    n_test_users = models.IntegerField(null=True)

    random_seed = models.IntegerField(default=42)


class EvaluationConfig(ModelWithInsDatetime):
    name = models.CharField(max_length=256, null=True)
    cutoff = models.IntegerField(default=20)

    class TargetMetric(models.TextChoices):
        NDCG = "ndcg", "Normalized discounted cumulative gain"
        MAP = "map", "mean average precision"
        RECALL = "recall", "recall"
        HIT = "hit", "hit"

    target_metric = models.CharField(
        choices=TargetMetric.choices, max_length=10, default=TargetMetric.NDCG
    )


class ModelConfiguration(ModelWithInsDatetime):
    name = models.CharField(max_length=256, null=True, unique=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    recommender_class_name = models.CharField(max_length=128)
    parameters_json = models.TextField()


class TrainedModel(ModelWithInsDatetime, BaseFileModel):
    configuration = models.ForeignKey(ModelConfiguration, on_delete=models.CASCADE)
    data_loc = models.ForeignKey(TrainingData, on_delete=models.CASCADE)
    irspack_version = models.CharField(max_length=16, null=True)


class ParameterTuningJob(ModelWithInsDatetime):
    data = models.ForeignKey(TrainingData, on_delete=models.CASCADE)
    split = models.ForeignKey(SplitConfig, on_delete=models.CASCADE)
    evaluation = models.ForeignKey(EvaluationConfig, on_delete=models.CASCADE)

    n_tasks_parallel = models.IntegerField(default=1)
    n_trials = models.IntegerField(default=40)
    memory_budget = models.IntegerField(default=8000)
    timeout_overall = models.IntegerField(null=True)
    timeout_singlestep = models.IntegerField(null=True)
    random_seed = models.IntegerField(null=True)
    tried_algorithms_json = models.TextField(null=True)

    irspack_version = models.CharField(max_length=16, null=True)

    train_after_tuning = models.BooleanField(default=True)
    tuned_model = models.OneToOneField(
        TrainedModel, null=True, on_delete=models.SET_NULL, related_name="tuning_job"
    )
    best_config = models.OneToOneField(
        ModelConfiguration,
        null=True,
        on_delete=models.SET_NULL,
        related_name="tuning_job",
    )
    best_score = models.FloatField(null=True)

    def study_name(self):
        return f"job-{self.id}-{self.ins_datetime}"


class TaskAndParameterJobLink(ModelWithInsDatetime):
    job = models.ForeignKey(
        ParameterTuningJob, on_delete=models.CASCADE, related_name="task_links"
    )
    task = models.OneToOneField(
        TaskResult, on_delete=models.CASCADE, related_name="tuning_job_link"
    )


class TaskAndTrainedModelLink(ModelWithInsDatetime):
    model = models.ForeignKey(
        TrainedModel, on_delete=models.CASCADE, related_name="task_links"
    )
    task = models.OneToOneField(
        TaskResult, on_delete=models.CASCADE, related_name="model_link"
    )


class TaskLog(ModelWithInsDatetime):
    task = models.ForeignKey(TaskResult, on_delete=models.CASCADE)
    contents = models.TextField(blank=True)
