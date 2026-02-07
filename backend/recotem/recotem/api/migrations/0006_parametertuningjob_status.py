from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0005_fix_model_config_name_uniqueness"),
    ]

    operations = [
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
    ]
