import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0008_add_updated_at"),
    ]

    operations = [
        # Project.name: TextField -> CharField(max_length=256)
        migrations.AlterField(
            model_name="project",
            name="name",
            field=models.CharField(max_length=256),
        ),
        # ItemMetaData.project: add db_index=True
        migrations.AlterField(
            model_name="itemmetadata",
            name="project",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                to="api.project",
                db_index=True,
            ),
        ),
        # ModelConfiguration.recommender_class_name: add validator
        migrations.AlterField(
            model_name="modelconfiguration",
            name="recommender_class_name",
            field=models.CharField(
                max_length=128,
                validators=[
                    django.core.validators.RegexValidator(
                        regex=r"^[A-Za-z_][A-Za-z0-9_]*$",
                        message="recommender_class_name must be a valid Python identifier.",
                    ),
                ],
            ),
        ),
    ]
