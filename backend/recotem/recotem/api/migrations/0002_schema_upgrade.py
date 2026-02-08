"""Squashed migration for schema changes added after initial setup.

Includes: owner/created_by fields, uniqueness constraints, status field,
indexes, validators, updated_at timestamps, model field improvements,
and TextField→JSONField conversion with data migration.
"""

import json

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def text_to_json_forward(apps, schema_editor):
    """Parse JSON strings stored in TextFields into native Python objects."""
    ModelConfiguration = apps.get_model("api", "ModelConfiguration")
    for obj in ModelConfiguration.objects.all().iterator():
        if isinstance(obj.parameters_json, str):
            try:
                obj.parameters_json = json.loads(obj.parameters_json)
            except (json.JSONDecodeError, TypeError):
                obj.parameters_json = {}
            obj.save(update_fields=["parameters_json"])

    ParameterTuningJob = apps.get_model("api", "ParameterTuningJob")
    for obj in ParameterTuningJob.objects.exclude(
        tried_algorithms_json__isnull=True
    ).iterator():
        if isinstance(obj.tried_algorithms_json, str):
            try:
                obj.tried_algorithms_json = json.loads(obj.tried_algorithms_json)
            except (json.JSONDecodeError, TypeError):
                obj.tried_algorithms_json = None
            obj.save(update_fields=["tried_algorithms_json"])

    ItemMetaData = apps.get_model("api", "ItemMetaData")
    for obj in ItemMetaData.objects.exclude(
        valid_columns_list_json__isnull=True
    ).iterator():
        if isinstance(obj.valid_columns_list_json, str):
            try:
                obj.valid_columns_list_json = json.loads(obj.valid_columns_list_json)
            except (json.JSONDecodeError, TypeError):
                obj.valid_columns_list_json = None
            obj.save(update_fields=["valid_columns_list_json"])


def json_to_text_reverse(apps, schema_editor):
    """Convert native Python objects back to JSON strings for rollback."""
    ModelConfiguration = apps.get_model("api", "ModelConfiguration")
    for obj in ModelConfiguration.objects.all().iterator():
        if not isinstance(obj.parameters_json, str):
            obj.parameters_json = json.dumps(obj.parameters_json)
            obj.save(update_fields=["parameters_json"])

    ParameterTuningJob = apps.get_model("api", "ParameterTuningJob")
    for obj in ParameterTuningJob.objects.exclude(
        tried_algorithms_json__isnull=True
    ).iterator():
        if not isinstance(obj.tried_algorithms_json, str):
            obj.tried_algorithms_json = json.dumps(obj.tried_algorithms_json)
            obj.save(update_fields=["tried_algorithms_json"])

    ItemMetaData = apps.get_model("api", "ItemMetaData")
    for obj in ItemMetaData.objects.exclude(
        valid_columns_list_json__isnull=True
    ).iterator():
        if not isinstance(obj.valid_columns_list_json, str):
            obj.valid_columns_list_json = json.dumps(obj.valid_columns_list_json)
            obj.save(update_fields=["valid_columns_list_json"])


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="projects",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="splitconfig",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="evaluationconfig",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="project",
            name="name",
            field=models.CharField(max_length=256),
        ),
        migrations.AddConstraint(
            model_name="project",
            constraint=models.UniqueConstraint(
                fields=("owner", "name"),
                name="unique_project_name_per_owner",
            ),
        ),
        migrations.AlterField(
            model_name="modelconfiguration",
            name="name",
            field=models.CharField(max_length=256, null=True),
        ),
        migrations.AddConstraint(
            model_name="modelconfiguration",
            constraint=models.UniqueConstraint(
                fields=("project", "name"),
                name="unique_model_config_name_per_project",
            ),
        ),
        migrations.AddField(
            model_name="parametertuningjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("RUNNING", "Running"),
                    ("COMPLETED", "Completed"),
                    ("FAILED", "Failed"),
                ],
                db_index=True,
                default="PENDING",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="trainingdata",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="api.project",
            ),
        ),
        migrations.AlterField(
            model_name="trainedmodel",
            name="configuration",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="api.modelconfiguration",
            ),
        ),
        migrations.AlterField(
            model_name="trainedmodel",
            name="data_loc",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="api.trainingdata",
            ),
        ),
        migrations.AlterField(
            model_name="parametertuningjob",
            name="data",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="api.trainingdata",
            ),
        ),
        migrations.AlterField(
            model_name="project",
            name="time_column",
            field=models.CharField(blank=True, max_length=256, null=True),
        ),
        migrations.AlterField(
            model_name="splitconfig",
            name="heldout_ratio",
            field=models.FloatField(
                default=0.1,
                validators=[
                    django.core.validators.MinValueValidator(0.0),
                    django.core.validators.MaxValueValidator(1.0),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="splitconfig",
            name="test_user_ratio",
            field=models.FloatField(
                default=1.0,
                validators=[
                    django.core.validators.MinValueValidator(0.0),
                    django.core.validators.MaxValueValidator(1.0),
                ],
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="trainingdata",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="itemmetadata",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="splitconfig",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="evaluationconfig",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="modelconfiguration",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="trainedmodel",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="parametertuningjob",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="taskandparameterjoblink",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="taskandtrainedmodellink",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="tasklog",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name="itemmetadata",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="api.project",
            ),
        ),
        migrations.AlterField(
            model_name="modelconfiguration",
            name="recommender_class_name",
            field=models.CharField(
                max_length=128,
                validators=[
                    django.core.validators.RegexValidator(
                        message="recommender_class_name must be a valid Python identifier.",
                        regex="^[A-Za-z_][A-Za-z0-9_]*$",
                    )
                ],
            ),
        ),
        migrations.AlterField(
            model_name="modelconfiguration",
            name="parameters_json",
            field=models.JSONField(default=dict),
        ),
        migrations.AlterField(
            model_name="parametertuningjob",
            name="tried_algorithms_json",
            field=models.JSONField(null=True),
        ),
        migrations.AlterField(
            model_name="itemmetadata",
            name="valid_columns_list_json",
            field=models.JSONField(null=True),
        ),
        migrations.RunPython(
            text_to_json_forward,
            json_to_text_reverse,
        ),
        migrations.AlterField(
            model_name="modelconfiguration",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="api.project",
            ),
        ),
    ]
