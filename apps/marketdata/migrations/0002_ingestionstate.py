from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("marketdata", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="IngestionState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=64, unique=True)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("cooldown_until", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name="ingestionstate",
            index=models.Index(fields=["key"], name="idx_ingestion_state_key"),
        ),
    ]
