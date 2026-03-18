from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("portfolios", "0002_evidencelifecycleautomationrun"),
    ]

    operations = [
        migrations.AddField(
            model_name="holdingtransaction",
            name="broker_confirmation_linked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="holdingtransaction",
            name="broker_confirmation_resolution",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="linked_transactions", to="portfolios.brokerpositionimportresolution"),
        ),
        migrations.AddField(
            model_name="holdingtransaction",
            name="broker_confirmation_run",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="linked_transactions", to="portfolios.brokerpositionimportrun"),
        ),
        migrations.AddField(
            model_name="holdingtransaction",
            name="broker_confirmation_snapshot",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="linked_transactions", to="portfolios.importedbrokersnapshot"),
        ),
    ]
