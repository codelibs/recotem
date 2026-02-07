from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0004_fix_project_name_uniqueness"),
    ]

    operations = [
        # Remove the global unique constraint on ModelConfiguration.name
        migrations.AlterField(
            model_name="modelconfiguration",
            name="name",
            field=models.CharField(max_length=256, null=True),
        ),
        # Add per-project unique constraint
        migrations.AddConstraint(
            model_name="modelconfiguration",
            constraint=models.UniqueConstraint(
                fields=["project", "name"],
                name="unique_model_config_name_per_project",
            ),
        ),
    ]
