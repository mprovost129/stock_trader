from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("marketdata", "0002_ingestionstate"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="IngestionJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("watchlist_name", models.CharField(default="Default", max_length=100)),
                ("source", models.CharField(choices=[("DATA_FRESHNESS", "Data freshness"), ("OPERATOR", "Operator"), ("MANUAL", "Manual")], default="MANUAL", max_length=24)),
                ("asset_class", models.CharField(blank=True, default="", max_length=16)),
                ("stock_timeframe", models.CharField(default="1d", max_length=4)),
                ("crypto_timeframe", models.CharField(default="1d", max_length=4)),
                ("stock_provider", models.CharField(blank=True, default="", max_length=32)),
                ("crypto_provider", models.CharField(blank=True, default="", max_length=32)),
                ("symbols_csv", models.TextField(blank=True, default="")),
                ("limit", models.PositiveIntegerField(default=300)),
                ("max_symbols", models.PositiveIntegerField(default=8)),
                ("throttle_seconds", models.FloatField(default=1.0)),
                ("run_after", models.DateTimeField(default=django.utils.timezone.now)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("RUNNING", "Running"), ("SUCCEEDED", "Succeeded"), ("FAILED", "Failed")], default="PENDING", max_length=16)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("max_attempts", models.PositiveIntegerField(default=1)),
                ("last_error", models.TextField(blank=True, default="")),
                ("result_summary", models.JSONField(blank=True, default=dict)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ingestion_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.AddIndex(
            model_name="ingestionjob",
            index=models.Index(fields=["status", "run_after"], name="idx_ingestjob_status_runafter"),
        ),
        migrations.AddIndex(
            model_name="ingestionjob",
            index=models.Index(fields=["user", "-created_at"], name="idx_ingestjob_user_recent"),
        ),
    ]

