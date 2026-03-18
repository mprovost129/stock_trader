from django.contrib import admin

from apps.marketdata.models import Instrument, PriceBar


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
