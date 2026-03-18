from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("portfolios", "0003_holdingtransaction_broker_confirmation_links"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PortfolioHealthSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("overall_score", models.PositiveIntegerField(default=100)),
                ("overall_grade_code", models.CharField(blank=True, default="", max_length=16)),
                ("overall_grade_label", models.CharField(blank=True, default="", max_length=32)),
                ("attention_count", models.PositiveIntegerField(default=0)),
                ("urgent_count", models.PositiveIntegerField(default=0)),
                ("weakest_account_label", models.CharField(blank=True, default="", max_length=80)),
                ("weakest_account_score", models.PositiveIntegerField(blank=True, null=True)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="portfolio_health_snapshots", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.AddIndex(
            model_name="portfoliohealthsnapshot",
            index=models.Index(fields=["user", "-created_at"], name="idx_port_health_recent"),
        ),
    ]
