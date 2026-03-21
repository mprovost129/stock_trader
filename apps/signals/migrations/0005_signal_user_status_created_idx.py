from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("signals", "0004_alter_operatornotification_kind"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="signal",
            index=models.Index(fields=["created_by", "status", "-generated_at"], name="idx_signal_user_stat_ct"),
        ),
    ]
