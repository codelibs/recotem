from pathlib import Path
from typing import Any, Optional, Type

import pandas as pd
from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from django_celery_results.models import TaskResult
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError

from .utils import read_dataframe

# Create your models here.


class Project(models.Model):
    name = models.TextField(unique=True)
    user_column = models.CharField(max_length=256)
    item_column = models.CharField(max_length=256)
    time_column = models.CharField(max_length=256, blank=False, null=True)
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)


class TrainingData(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    upload_path = models.FileField(upload_to="training_data/", null=False)
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)

    def validate_return_df(self) -> pd.DataFrame:
        pathname = Path(self.upload_path.name)
        df = read_dataframe(pathname, self.upload_path)
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


class SplitConfig(models.Model):
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


class EvaluationConfig(models.Model):
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


class ModelConfiguration(models.Model):
    name = models.CharField(max_length=256, null=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    recommender_class_name = models.CharField(max_length=128)
    parameters_json = models.TextField()
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)


class TrainedModel(models.Model):
    name = models.CharField(max_length=256, null=True)
    configuration = models.ForeignKey(ModelConfiguration, on_delete=models.CASCADE)
    data_loc = models.ForeignKey(TrainingData, on_delete=models.CASCADE)
    model_path = models.FileField(upload_to="models/", null=True)
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)


@receiver(post_save, sender=TrainedModel)
def run_training_after_creation(
    sender: Type[TrainedModel],
    instance: Optional[TrainedModel] = None,
    created: bool = False,
    **kwargs: Any,
) -> None:
    if created and instance is not None:
        from .tasks import train_recommender

        train_recommender.delay(instance.id)


class ParameterTuningJob(models.Model):
    name = models.CharField(max_length=256, null=True)
    data = models.ForeignKey(TrainingData, on_delete=models.CASCADE)
    split = models.ForeignKey(SplitConfig, null=True, on_delete=models.CASCADE)
    evaluation = models.ForeignKey(EvaluationConfig, on_delete=models.CASCADE)

    n_tasks_parallel = models.IntegerField(default=1)
    n_trials = models.IntegerField(default=40)
    memory_budget = models.IntegerField(default=8000)
    timeout_overall = models.IntegerField(null=True)
    timeout_singlestep = models.IntegerField(null=True)
    random_seed = models.IntegerField(null=True)

    best_config = models.ForeignKey(
        ModelConfiguration, null=True, on_delete=models.PROTECT
    )
    tuned_model = models.ForeignKey(TrainedModel, null=True, on_delete=models.PROTECT)
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)

    def study_name(self):
        return f"job-{self.id}-{self.ins_datetime}"


@receiver(post_save, sender=ParameterTuningJob)
def run_tuning_job_after_creation(
    sender: Type[ParameterTuningJob],
    instance: Optional[ParameterTuningJob] = None,
    created: bool = False,
    **kwargs: Any,
) -> None:
    if created and instance is not None:
        from .tasks import start_tuning_job

        start_tuning_job(instance)


class TaskAndParameterJobLink(models.Model):
    job = models.ForeignKey(ParameterTuningJob, on_delete=models.CASCADE)
    task = models.ForeignKey(TaskResult, on_delete=models.CASCADE)
    ins_datetime = models.DateTimeField(auto_now_add=True)


class TaskAndTrainedModelLink(models.Model):
    model = models.ForeignKey(TrainedModel, on_delete=models.CASCADE)
    task = models.ForeignKey(TaskResult, on_delete=models.CASCADE)
    ins_datetime = models.DateTimeField(auto_now_add=True)


class TaskLog(models.Model):
    task = models.ForeignKey(TaskResult, on_delete=models.CASCADE)
    contents = models.TextField(blank=True)
    ins_datetime = models.DateTimeField(auto_now_add=True)
