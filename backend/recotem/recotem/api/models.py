from typing import Any, Type, Optional

from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from rest_framework.authtoken.models import Token

from django.contrib.auth.models import User

# Create your models here.


@receiver(post_save, sender=User)
def create_auth_token(
    sender: Type[User],
    instance: Optional[User] = None,
    created: bool = False,
    **kwargs: Any
) -> None:
    if created:
        Token.objects.create(user=instance)


class Project(models.Model):
    name = models.TextField(unique=True)
    user_column = models.CharField(max_length=256)
    item_column = models.CharField(max_length=256)
    time_column = models.CharField(max_length=256, blank=True, null=True)
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)


class TrainingData(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    upload_path = models.FileField(upload_to="training_data/", null=False)
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)


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
        NDCG = "NDCG", "Normalized discounted cumulative gain"
        MAP = "MAP", "mean average precision"
        RECALL = "recall", "recall"
        HIT = "hit", "hit"

    scheme = models.CharField(
        choices=TargetMetric.choices, max_length=10, default=TargetMetric.NDCG
    )


class ModelConfiguration(models.Model):
    name = models.CharField(max_length=256, null=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    parameters_json = models.TextField()
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)


class TrainedModel(models.Model):
    configuration = models.ForeignKey(ModelConfiguration, on_delete=models.CASCADE)
    data_loc = models.ForeignKey(TrainingData, on_delete=models.CASCADE)
    model_path = models.FileField(upload_to="models/")
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)


class ParameterTuningJob(models.Model):
    data = models.ForeignKey(TrainingData, on_delete=models.CASCADE)
    split = models.ForeignKey(SplitConfig, null=True, on_delete=models.CASCADE)
    evaluation = models.ForeignKey(EvaluationConfig, on_delete=models.CASCADE)
    best_config = models.ForeignKey(
        ModelConfiguration, null=True, on_delete=models.CASCADE
    )
    tuned_model = models.ForeignKey(TrainedModel, null=True, on_delete=models.CASCADE)
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)


class ParameterTuningLog(models.Model):
    job = models.ForeignKey(ParameterTuningJob, on_delete=models.CASCADE)
    log_str = models.TextField()
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)
