from django.contrib import admin

from apps.marketdata.models import IngestionJob, Instrument, PriceBar


@admin.register(Instrument)
class InstrumentAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name", "asset_class", "exchange", "is_active", "updated_at")
    list_filter = ("asset_class", "is_active")
    search_fields = ("symbol", "name")


@admin.register(PriceBar)
class PriceBarAdmin(admin.ModelAdmin):
    list_display = ("instrument", "timeframe", "ts", "open", "high", "low", "close", "volume")
    list_filter = ("timeframe",)
    search_fields = ("instrument__symbol",)
    ordering = ("-ts",)


@admin.register(IngestionJob)
class IngestionJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "source",
        "asset_class",
        "status",
        "watchlist_name",
        "max_symbols",
        "throttle_seconds",
        "run_after",
        "created_at",
    )
    list_filter = ("status", "source", "asset_class", "crypto_timeframe", "stock_timeframe")
    search_fields = ("user__username", "watchlist_name", "symbols_csv", "last_error")
    ordering = ("-created_at",)
