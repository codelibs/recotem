"""Migrate TextField-based JSON columns to JSONField.

Converts parameters_json, tried_algorithms_json, and valid_columns_list_json
from TextField (storing JSON strings) to JSONField (native Python objects).
Also adds db_index to ModelConfiguration.project.
"""

import json

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
    for obj in ParameterTuningJob.objects.exclude(tried_algorithms_json__isnull=True).iterator():
        if isinstance(obj.tried_algorithms_json, str):
            try:
                obj.tried_algorithms_json = json.loads(obj.tried_algorithms_json)
            except (json.JSONDecodeError, TypeError):
                obj.tried_algorithms_json = None
            obj.save(update_fields=["tried_algorithms_json"])

    ItemMetaData = apps.get_model("api", "ItemMetaData")
    for obj in ItemMetaData.objects.exclude(valid_columns_list_json__isnull=True).iterator():
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
    for obj in ParameterTuningJob.objects.exclude(tried_algorithms_json__isnull=True).iterator():
        if not isinstance(obj.tried_algorithms_json, str):
            obj.tried_algorithms_json = json.dumps(obj.tried_algorithms_json)
            obj.save(update_fields=["tried_algorithms_json"])

    ItemMetaData = apps.get_model("api", "ItemMetaData")
    for obj in ItemMetaData.objects.exclude(valid_columns_list_json__isnull=True).iterator():
        if not isinstance(obj.valid_columns_list_json, str):
            obj.valid_columns_list_json = json.dumps(obj.valid_columns_list_json)
            obj.save(update_fields=["valid_columns_list_json"])


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0009_model_field_improvements"),
    ]

    operations = [
        # Step 1: Convert TextField to JSONField
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
        # Step 2: Run data migration to parse existing JSON strings
        migrations.RunPython(text_to_json_forward, json_to_text_reverse),
        # Step 3: Add db_index to ModelConfiguration.project
        migrations.AlterField(
            model_name="modelconfiguration",
            name="project",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                to="api.project",
                db_index=True,
            ),
        ),
    ]
