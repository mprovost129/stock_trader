from django.contrib import admin

from .models import Strategy, StrategyRunConfig


@admin.register(Strategy)
class StrategyAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "is_enabled", "updated_at")
    search_fields = ("slug", "name")
    list_filter = ("is_enabled",)


@admin.register(StrategyRunConfig)
class StrategyRunConfigAdmin(admin.ModelAdmin):
    list_display = ("strategy", "timeframe", "is_active", "updated_at")
    list_filter = ("timeframe", "is_active")
    search_fields = ("strategy__slug", "strategy__name")
