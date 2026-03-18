from django.contrib import admin

from .models import AccountRetentionPolicyOverride, AccountRetentionPolicyTemplate, BrokerPositionImportResolution, BrokerPositionImportRun, EvidenceLifecycleAutomationRun, HeldPosition, HoldingAlert, HoldingTransaction, ImportedBrokerSnapshot, InstrumentSelection, PortfolioHealthSnapshot, SavedFilterPreset, UserRiskProfile, Watchlist


class InstrumentSelectionInline(admin.TabularInline):
    model = InstrumentSelection
    extra = 0
    autocomplete_fields = ("instrument",)
    fields = ("instrument", "is_active", "priority", "sector", "note")


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "is_active", "created_at", "updated_at")
    search_fields = ("user__username", "name")
    list_filter = ("is_active",)
    inlines = [InstrumentSelectionInline]


@admin.register(UserRiskProfile)
class UserRiskProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "account_equity", "risk_per_trade_pct", "max_position_weight_pct", "max_sector_weight_pct", "concentration_warning_buffer_pct", "max_high_correlation_positions", "high_correlation_threshold", "correlation_lookback_bars", "max_net_exposure_pct", "net_exposure_warning_buffer_pct", "require_stop_for_open_positions", "max_stop_loss_pct", "stop_warning_buffer_pct", "drawdown_review_pct", "drawdown_urgent_pct", "stop_policy_target_hours", "updated_at")
    search_fields = ("user__username",)


@admin.register(SavedFilterPreset)
class SavedFilterPresetAdmin(admin.ModelAdmin):
    list_display = ("user", "scope", "name", "is_dashboard_widget", "updated_at")
    list_filter = ("scope", "is_dashboard_widget")
    search_fields = ("user__username", "name")
    ordering = ("user__username", "scope", "name")






@admin.register(AccountRetentionPolicyTemplate)
class AccountRetentionPolicyTemplateAdmin(admin.ModelAdmin):
    list_display = ("user", "template_name", "family_label", "evidence_retention_default_days", "evidence_retention_verified_days", "updated_at")
    list_filter = ("family_label",)
    search_fields = ("user__username", "template_name", "family_label", "notes")


@admin.register(AccountRetentionPolicyOverride)
class AccountRetentionPolicyOverrideAdmin(admin.ModelAdmin):
    list_display = ("user", "account_label", "source_template", "evidence_retention_default_days", "evidence_retention_verified_days", "evidence_retention_confirmation_days", "updated_at")
    search_fields = ("user__username", "account_label", "source_template__template_name")
    ordering = ("user__username", "account_label")

@admin.register(ImportedBrokerSnapshot)
class ImportedBrokerSnapshotAdmin(admin.ModelAdmin):
    list_display = ("user", "account_label", "source_label", "as_of", "account_equity", "cash_balance", "created_at")
    search_fields = ("user__username", "account_label", "source_label", "notes")
    ordering = ("-as_of", "-id")



@admin.register(HeldPosition)
class HeldPositionAdmin(admin.ModelAdmin):
    list_display = ("user", "account_label", "instrument", "status", "source", "missing_from_latest_import", "quantity", "average_entry_price", "last_price", "pnl_pct", "last_import_seen_at", "updated_at")
    list_filter = ("status", "source", "missing_from_latest_import", "instrument__asset_class", "account_label")
    search_fields = ("user__username", "account_label", "instrument__symbol", "thesis", "notes")
    ordering = ("-updated_at",)


@admin.register(HoldingAlert)
class HoldingAlertAdmin(admin.ModelAdmin):
    list_display = ("created_at", "position", "alert_type", "channel", "status", "reason")
    list_filter = ("alert_type", "channel", "status")
    search_fields = ("position__instrument__symbol", "message", "error_message")
    ordering = ("-created_at",)



@admin.register(HoldingTransaction)
class HoldingTransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "position", "event_type", "account_label_snapshot", "risk_guardrail_posture_snapshot", "stop_policy_status", "stop_policy_reason_code", "execution_evidence_type", "execution_evidence_quality", "broker_confirmation_linked_at", "quantity", "price", "realized_pnl_amount")
    list_filter = ("event_type", "risk_guardrail_posture_snapshot", "stop_policy_status", "stop_policy_reason_code", "account_label_snapshot", "execution_evidence_type", "execution_evidence_quality", "broker_confirmation_linked_at")
    search_fields = ("position__instrument__symbol", "account_label_snapshot", "notes", "risk_guardrail_reason_snapshot", "execution_evidence_reference", "execution_evidence_note", "broker_confirmation_snapshot__source_label", "broker_confirmation_run__source_label", "broker_confirmation_resolution__symbol")
    ordering = ("-created_at",)


@admin.register(InstrumentSelection)
class InstrumentSelectionAdmin(admin.ModelAdmin):
    list_display = ("watchlist", "instrument", "is_active", "priority", "sector", "note")
    list_filter = ("is_active", "priority", "instrument__asset_class")
    search_fields = ("watchlist__name", "watchlist__user__username", "instrument__symbol", "sector", "note")
    ordering = ("watchlist__user__username", "watchlist__name", "-priority", "instrument__symbol")


@admin.register(BrokerPositionImportRun)
class BrokerPositionImportRunAdmin(admin.ModelAdmin):
    list_display = ("user", "account_label", "source_label", "uploaded_filename", "unresolved_count", "created_at")
    search_fields = ("user__username", "account_label", "source_label", "uploaded_filename")
    ordering = ("-created_at", "-id")


@admin.register(BrokerPositionImportResolution)
class BrokerPositionImportResolutionAdmin(admin.ModelAdmin):
    list_display = ("user", "run", "symbol", "action", "resolved_at")
    list_filter = ("action",)
    search_fields = ("user__username", "symbol", "note")
    ordering = ("-resolved_at", "-id")


@admin.register(EvidenceLifecycleAutomationRun)
class EvidenceLifecycleAutomationRunAdmin(admin.ModelAdmin):
    list_display = ("user", "archive_expired", "scanned_count", "attachment_count", "expiring_soon_count", "expired_count", "archived_count", "created_at")
    list_filter = ("archive_expired",)
    search_fields = ("user__username", "notes")
    ordering = ("-created_at", "-id")


@admin.register(PortfolioHealthSnapshot)
class PortfolioHealthSnapshotAdmin(admin.ModelAdmin):
    list_display = ("user", "overall_score", "overall_grade_label", "attention_count", "urgent_count", "weakest_account_label", "created_at")
    list_filter = ("overall_grade_code",)
    search_fields = ("user__username", "weakest_account_label")
    ordering = ("-created_at", "-id")
