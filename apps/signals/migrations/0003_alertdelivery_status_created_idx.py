from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("signals", "0002_alter_operatornotification_kind"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="alertdelivery",
            index=models.Index(fields=["status", "-created_at"], name="idx_alert_status_ct"),
        ),
    ]
