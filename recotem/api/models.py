from typing import Any, Type, Optional
from django.conf import settings
from django.core.files.uploadhandler import TemporaryFileUploadHandler
from django.db import models
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from rest_framework.authtoken.models import Token

from django.contrib.auth.models import User


@receiver(post_save, sender=User)
def create_auth_token(
    sender: Type[User],
    instance: Optional[User] = None,
    created: bool = False,
    **kwargs: Any
) -> None:
    if created:
        Token.objects.create(user=instance)


# Create your models here.


class Project(models.Model):
    project_name = models.TextField(unique=True)
    user_column = models.CharField(max_length=256)
    item_column = models.CharField(max_length=256)
    time_column = models.CharField(max_length=256, blank=True, null=True)

    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)


class TrainingData(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    upload_path = models.FileField(upload_to="training_data/")
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)


class ModelConfiguration(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    parameters_json = models.TextField()


class EvaluationConfig(models.Model):
    scheme = models.CharField(
        choices=[
            ("RG", "RANDOM_GLOBAL"),
            ("RU", "RANDOM_USER"),
            ("TG", "TIME_GLOBAL"),
            ("TU", "TIME_USER"),
        ],
        max_length=3,
    )
    split_args_json = models.TextField()


class TrainedModel(models.Model):
    configuration = models.ForeignKey(ModelConfiguration, on_delete=models.CASCADE)
    data_loc = models.ForeignKey(TrainingData, on_delete=models.CASCADE)
    model_path = models.FileField(upload_to="models/")


@receiver(signal=post_delete, sender=TrainingData)
def delete_file(
    sender: Type[TrainingData], instance: TrainingData, **kwargs: Any
) -> None:
    instance.upload_path.delete()
    pass


class ParameterTuningJob(models.Model):
    data = models.ForeignKey(TrainingData, on_delete=models.CASCADE)
    evaluation = models.ForeignKey(EvaluationConfig, on_delete=models.CASCADE)
    best_config = models.ForeignKey(
        ModelConfiguration, null=True, on_delete=models.CASCADE
    )
    tuned_model = models.ForeignKey(TrainedModel, null=True, on_delete=models.CASCADE)


class ParameterTuningLog(models.Model):
    job = models.ForeignKey(ParameterTuningJob, on_delete=models.CASCADE)
    log_str = models.TextField()
    ins_datetime = models.DateTimeField(auto_now_add=True)
    upd_datetime = models.DateTimeField(auto_now=True)
