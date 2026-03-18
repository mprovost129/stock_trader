from django.contrib import admin

from .models import AlertDelivery, OperatorNotification, PaperTrade, PositionAlert, Signal, SignalOutcome, TradePlan


class TradePlanInline(admin.StackedInline):
    model = TradePlan
    extra = 0


class AlertDeliveryInline(admin.TabularInline):
    model = AlertDelivery
    extra = 0
    readonly_fields = ("channel", "status", "reason", "delivered_at", "created_at", "error_message")


class SignalOutcomeInline(admin.StackedInline):
    model = SignalOutcome
    extra = 0
    readonly_fields = ("status", "outcome_label", "lookahead_bars", "bars_observed", "reference_price", "return_pct", "max_favorable_excursion_pct", "max_adverse_excursion_pct", "target_1_hit", "target_2_hit", "stop_hit", "evaluated_at", "created_at", "updated_at")


class PaperTradeInline(admin.StackedInline):
    model = PaperTrade
    extra = 0
    readonly_fields = ("status", "entry_time", "exit_time", "pnl_amount", "pnl_pct", "created_at", "updated_at")


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display = (
        "generated_at",
        "instrument",
        "strategy",
        "timeframe",
        "direction",
        "signal_kind",
        "signal_label",
        "status",
        "score",
        "score_component_summary",
    )
    list_filter = ("status", "direction", "signal_kind", "signal_label", "timeframe", "strategy")
    search_fields = ("instrument__symbol", "strategy__slug", "strategy__name")
    ordering = ("-generated_at",)
    inlines = [TradePlanInline, PaperTradeInline, SignalOutcomeInline, AlertDeliveryInline]

    @admin.display(description="Score breakdown")
    def score_component_summary(self, obj):
        if not obj.score_components:
            return "—"
        return ", ".join(f"{k}:{float(v):.1f}" for k, v in list(obj.score_components.items())[:3])


@admin.register(TradePlan)
class TradePlanAdmin(admin.ModelAdmin):
    list_display = ("signal", "entry_price", "stop_price", "suggested_qty", "created_at")
    search_fields = ("signal__instrument__symbol",)


@admin.register(PaperTrade)
class PaperTradeAdmin(admin.ModelAdmin):
    list_display = ("signal", "status", "entry_price", "exit_price", "pnl_amount", "pnl_pct", "updated_at")
    list_filter = ("status", "target_1_hit", "target_2_hit", "stop_triggered")
    search_fields = ("signal__instrument__symbol", "signal__strategy__slug")
    ordering = ("-updated_at",)


@admin.register(PositionAlert)
class PositionAlertAdmin(admin.ModelAdmin):
    list_display = ("created_at", "paper_trade", "alert_type", "status", "reason")
    list_filter = ("alert_type", "status")
    search_fields = ("paper_trade__signal__instrument__symbol", "reason", "error_message")
    ordering = ("-created_at",)


@admin.register(AlertDelivery)
class AlertDeliveryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "signal", "channel", "status", "reason", "delivered_at")
    list_filter = ("channel", "status", "reason")
    search_fields = ("signal__instrument__symbol", "signal__strategy__slug", "error_message")
    ordering = ("-created_at",)


@admin.register(SignalOutcome)
class SignalOutcomeAdmin(admin.ModelAdmin):
    list_display = ("signal", "status", "outcome_label", "bars_observed", "return_pct", "max_favorable_excursion_pct", "max_adverse_excursion_pct", "target_1_hit", "target_2_hit", "stop_hit", "evaluated_at")
    list_filter = ("status", "outcome_label", "target_1_hit", "target_2_hit", "stop_hit")
    search_fields = ("signal__instrument__symbol", "signal__strategy__slug", "signal__strategy__name")
    ordering = ("-updated_at",)


@admin.register(OperatorNotification)
class OperatorNotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "channel", "status", "reason", "headline", "delivered_at")
    list_filter = ("kind", "channel", "status", "reason")
    search_fields = ("headline", "body", "error_message")
    ordering = ("-created_at",)
