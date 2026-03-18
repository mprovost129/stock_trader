from django.apps import AppConfig


class StrategiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.strategies"
    verbose_name = "Strategies"

    def ready(self) -> None:
        # Import strategy implementations so @register decorators execute.
        from . import implementations  # noqa: F401
