import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0006_parametertuningjob_status"),
    ]

    operations = [
        # Add db_index to FK fields for query performance
        migrations.AlterField(
            model_name="trainingdata",
            name="project",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                to="api.project",
                db_index=True,
            ),
        ),
        migrations.AlterField(
            model_name="trainedmodel",
            name="configuration",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                to="api.modelconfiguration",
                db_index=True,
            ),
        ),
        migrations.AlterField(
            model_name="trainedmodel",
            name="data_loc",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                to="api.trainingdata",
                db_index=True,
            ),
        ),
        migrations.AlterField(
            model_name="parametertuningjob",
            name="data",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                to="api.trainingdata",
                db_index=True,
            ),
        ),
        # Fix time_column: null=True should have blank=True
        migrations.AlterField(
            model_name="project",
            name="time_column",
            field=models.CharField(blank=True, max_length=256, null=True),
        ),
        # Add model-level validators for ratio fields
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
    ]
