from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
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
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-id"]


class Project(ModelWithInsDatetime):
    name = models.CharField(max_length=256)
    # Legacy data created before multi-user support has owner=NULL.
    # These "unowned" projects are visible to all authenticated users
    # (see OwnedResourceMixin).  Do NOT change null=True without a
    # data migration that assigns owners to all existing rows.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
        null=True,
        blank=True,
    )
    user_column = models.CharField(max_length=256)
    item_column = models.CharField(max_length=256)
    time_column = models.CharField(max_length=256, blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"],
                name="unique_project_name_per_owner",
            ),
        ]


class TrainingData(ModelWithInsDatetime, BaseFileModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_index=True)

    def validate_return_df(self) -> pd.DataFrame:
        pathname = Path(self.file.name)
        df = read_dataframe(pathname, self.file)
        user_column: str = self.project.user_column
        item_column: str = self.project.item_column
        time_column: str | None = self.project.time_column

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
                ) from None

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
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_index=True)
    valid_columns_list_json = models.JSONField(null=True)

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
    sender, instance: TrainingData | None = None, created: bool = False, **kwargs
) -> None:
    if not created:
        return
    if not bool(instance.file):
        return
    instance.filesize = instance.file.size
    instance.save()


class SplitConfig(ModelWithInsDatetime):
    name = models.CharField(max_length=256, null=True)
    # null=True for legacy rows created before multi-user support.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class SplitScheme(models.TextChoices):
        RANDOM = "RG", _("Random")
        TIME_GLOBAL = "TG", _("Time Global")
        TIME_USER = "TU", _("Time User")

    scheme = models.CharField(
        choices=SplitScheme.choices, max_length=2, default=SplitScheme.RANDOM
    )
    heldout_ratio = models.FloatField(
        default=0.1,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    n_heldout = models.IntegerField(null=True)

    test_user_ratio = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    n_test_users = models.IntegerField(null=True)

    random_seed = models.IntegerField(default=42)


class EvaluationConfig(ModelWithInsDatetime):
    name = models.CharField(max_length=256, null=True)
    cutoff = models.IntegerField(default=20)
    # null=True for legacy rows created before multi-user support.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class TargetMetric(models.TextChoices):
        NDCG = "ndcg", "Normalized discounted cumulative gain"
        MAP = "map", "mean average precision"
        RECALL = "recall", "recall"
        HIT = "hit", "hit"

    target_metric = models.CharField(
        choices=TargetMetric.choices, max_length=10, default=TargetMetric.NDCG
    )


_recommender_class_validator = RegexValidator(
    regex=r"^[A-Za-z_][A-Za-z0-9_]*$",
    message="recommender_class_name must be a valid Python identifier.",
)


class ModelConfiguration(ModelWithInsDatetime):
    name = models.CharField(max_length=256, null=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_index=True)
    recommender_class_name = models.CharField(
        max_length=128, validators=[_recommender_class_validator]
    )
    parameters_json = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                name="unique_model_config_name_per_project",
            ),
        ]


class TrainedModel(ModelWithInsDatetime, BaseFileModel):
    configuration = models.ForeignKey(
        ModelConfiguration, on_delete=models.CASCADE, db_index=True
    )
    data_loc = models.ForeignKey(TrainingData, on_delete=models.CASCADE, db_index=True)
    irspack_version = models.CharField(max_length=16, null=True)


class ParameterTuningJob(ModelWithInsDatetime):
    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        RUNNING = "RUNNING", _("Running")
        COMPLETED = "COMPLETED", _("Completed")
        FAILED = "FAILED", _("Failed")

    data = models.ForeignKey(TrainingData, on_delete=models.CASCADE, db_index=True)
    split = models.ForeignKey(SplitConfig, on_delete=models.CASCADE)
    evaluation = models.ForeignKey(EvaluationConfig, on_delete=models.CASCADE)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    n_tasks_parallel = models.IntegerField(default=1)
    n_trials = models.IntegerField(default=40)
    memory_budget = models.IntegerField(default=8000)
    timeout_overall = models.IntegerField(null=True)
    timeout_singlestep = models.IntegerField(null=True)
    random_seed = models.IntegerField(null=True)
    tried_algorithms_json = models.JSONField(null=True)

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


class ApiKey(ModelWithInsDatetime):
    """API key for programmatic access to project resources."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="api_keys"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=256)
    key_prefix = models.CharField(max_length=16, db_index=True)
    hashed_key = models.CharField(max_length=256)
    scopes = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                name="unique_api_key_name_per_project",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.key_prefix}...)"


class TaskLog(ModelWithInsDatetime):
    task = models.ForeignKey(TaskResult, on_delete=models.CASCADE)
    contents = models.TextField(blank=True)


class RetrainingSchedule(ModelWithInsDatetime):
    """Schedule for periodic model retraining."""

    class RunStatus(models.TextChoices):
        SUCCESS = "SUCCESS", _("Success")
        FAILED = "FAILED", _("Failed")
        SKIPPED = "SKIPPED", _("Skipped")

    project = models.OneToOneField(
        Project, on_delete=models.CASCADE, related_name="retraining_schedule"
    )
    is_enabled = models.BooleanField(default=False)
    cron_expression = models.CharField(max_length=100, default="0 2 * * 0")
    training_data = models.ForeignKey(
        TrainingData, on_delete=models.SET_NULL, null=True, blank=True
    )
    model_configuration = models.ForeignKey(
        ModelConfiguration, on_delete=models.SET_NULL, null=True, blank=True
    )
    retune = models.BooleanField(default=False)
    split_config = models.ForeignKey(
        SplitConfig, on_delete=models.SET_NULL, null=True, blank=True
    )
    evaluation_config = models.ForeignKey(
        EvaluationConfig, on_delete=models.SET_NULL, null=True, blank=True
    )
    max_retries = models.IntegerField(default=3)
    notify_on_failure = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(
        max_length=10,
        choices=RunStatus.choices,
        null=True,
        blank=True,
    )
    next_run_at = models.DateTimeField(null=True, blank=True)
    auto_deploy = models.BooleanField(default=False)


class RetrainingRun(ModelWithInsDatetime):
    """Record of a single retraining execution."""

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        RUNNING = "RUNNING", _("Running")
        COMPLETED = "COMPLETED", _("Completed")
        FAILED = "FAILED", _("Failed")
        SKIPPED = "SKIPPED", _("Skipped")

    schedule = models.ForeignKey(
        RetrainingSchedule, on_delete=models.CASCADE, related_name="runs"
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    trained_model = models.ForeignKey(
        TrainedModel, on_delete=models.SET_NULL, null=True, blank=True
    )
    tuning_job = models.ForeignKey(
        ParameterTuningJob, on_delete=models.SET_NULL, null=True, blank=True
    )
    error_message = models.TextField(blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)
    data_rows_at_trigger = models.IntegerField(null=True, blank=True)


class DeploymentSlot(ModelWithInsDatetime):
    """A deployment slot with a traffic weight for A/B testing."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="deployment_slots"
    )
    name = models.CharField(max_length=256)
    trained_model = models.ForeignKey(TrainedModel, on_delete=models.CASCADE)
    weight = models.FloatField(
        default=100,
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
    )
    is_active = models.BooleanField(default=True)


class ABTest(ModelWithInsDatetime):
    """A/B test comparing two deployment slots."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        RUNNING = "RUNNING", _("Running")
        COMPLETED = "COMPLETED", _("Completed")
        CANCELLED = "CANCELLED", _("Cancelled")

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="ab_tests"
    )
    name = models.CharField(max_length=256)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    control_slot = models.ForeignKey(
        DeploymentSlot, on_delete=models.CASCADE, related_name="control_tests"
    )
    variant_slot = models.ForeignKey(
        DeploymentSlot, on_delete=models.CASCADE, related_name="variant_tests"
    )
    target_metric_name = models.CharField(max_length=50, default="ctr")
    min_sample_size = models.IntegerField(default=1000)
    confidence_level = models.FloatField(
        default=0.95,
        validators=[MinValueValidator(0.5), MaxValueValidator(0.99)],
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    winner_slot = models.ForeignKey(
        DeploymentSlot,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="won_tests",
    )


class ConversionEvent(models.Model):
    """Tracking event for A/B test analysis (impression, click, purchase)."""

    class EventType(models.TextChoices):
        IMPRESSION = "impression", _("Impression")
        CLICK = "click", _("Click")
        PURCHASE = "purchase", _("Purchase")

    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    deployment_slot = models.ForeignKey(DeploymentSlot, on_delete=models.CASCADE)
    user_id = models.CharField(max_length=256)
    item_id = models.CharField(max_length=256, blank=True, default="")
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    recommendation_request_id = models.UUIDField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["project", "deployment_slot", "event_type", "timestamp"],
                name="idx_conversion_event_lookup",
            ),
        ]
