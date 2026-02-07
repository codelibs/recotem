from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("api", "0003_add_created_by_to_configs"),
    ]

    operations = [
        # Remove the global unique constraint on name
        migrations.AlterField(
            model_name="project",
            name="name",
            field=models.TextField(),
        ),
        # Add per-owner unique constraint
        migrations.AddConstraint(
            model_name="project",
            constraint=models.UniqueConstraint(
                fields=["owner", "name"],
                name="unique_project_name_per_owner",
            ),
        ),
    ]
