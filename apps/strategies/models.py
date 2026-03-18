from django.db import models


class Strategy(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    is_enabled = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class StrategyRunConfig(models.Model):
    """User/system configuration for running a strategy over a universe."""

    strategy = models.ForeignKey(Strategy, on_delete=models.CASCADE, related_name="run_configs")
    timeframe = models.CharField(max_length=8, default="1m")
    params = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.strategy.slug} ({self.timeframe})"
