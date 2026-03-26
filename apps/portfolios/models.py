from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.marketdata.models import Instrument


def holding_transaction_evidence_upload_to(instance, filename):
    symbol = getattr(getattr(instance, "position", None), "instrument", None)
    symbol_value = getattr(symbol, "symbol", "unknown") or "unknown"
    return f"portfolio_evidence/{instance.position.user_id}/{symbol_value}/{filename}"



class Watchlist(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=100, default="Default")
    is_active = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "name")

    def __str__(self) -> str:
        return f"{self.user} — {self.name}"


class InstrumentSelection(models.Model):
    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        NORMAL = "NORMAL", "Normal"
        HIGH = "HIGH", "High priority"

    watchlist = models.ForeignKey(Watchlist, on_delete=models.CASCADE, related_name="selections")
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.NORMAL)
    sector = models.CharField(max_length=80, blank=True, default="")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("watchlist", "instrument")

    def __str__(self) -> str:
        return f"{self.watchlist} — {self.instrument.symbol}"


class UserRiskProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    account_equity = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    risk_per_trade_pct = models.DecimalField(max_digits=6, decimal_places=4, default=0.0025)  # 0.25%
    max_position_weight_pct = models.DecimalField(max_digits=6, decimal_places=2, default=20)
    max_sector_weight_pct = models.DecimalField(max_digits=6, decimal_places=2, default=35)
    concentration_warning_buffer_pct = models.DecimalField(max_digits=6, decimal_places=2, default=5)
    max_high_correlation_positions = models.PositiveIntegerField(default=2)
    high_correlation_threshold = models.DecimalField(max_digits=4, decimal_places=2, default=0.80)
    correlation_lookback_bars = models.PositiveIntegerField(default=60)
    max_net_exposure_pct = models.DecimalField(max_digits=6, decimal_places=2, default=80)
    net_exposure_warning_buffer_pct = models.DecimalField(max_digits=6, decimal_places=2, default=10)
    require_stop_for_open_positions = models.BooleanField(default=True)
    max_stop_loss_pct = models.DecimalField(max_digits=6, decimal_places=2, default=8)
    stop_warning_buffer_pct = models.DecimalField(max_digits=6, decimal_places=2, default=1.50)
    drawdown_review_pct = models.DecimalField(max_digits=6, decimal_places=2, default=2.50)
    drawdown_urgent_pct = models.DecimalField(max_digits=6, decimal_places=2, default=5.00)
    stop_policy_target_hours = models.PositiveIntegerField(default=24)
    evidence_retention_default_days = models.PositiveIntegerField(default=365)
    evidence_retention_verified_days = models.PositiveIntegerField(default=730)
    evidence_retention_strong_days = models.PositiveIntegerField(default=365)
    evidence_retention_weak_days = models.PositiveIntegerField(default=180)
    evidence_retention_placeholder_days = models.PositiveIntegerField(default=90)
    evidence_retention_confirmation_days = models.PositiveIntegerField(default=730)
    evidence_retention_import_match_days = models.PositiveIntegerField(default=365)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"RiskProfile for {self.user}"


class EquityTransaction(models.Model):
    class TransactionType(models.TextChoices):
        DEPOSIT = "DEPOSIT", "Deposit"
        WITHDRAWAL = "WITHDRAWAL", "Withdrawal"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="equity_transactions")
    transaction_type = models.CharField(max_length=16, choices=TransactionType.choices)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    notes = models.TextField(blank=True)
    balance_after = models.DecimalField(max_digits=20, decimal_places=2)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="idx_equity_tx_user_ts"),
        ]

    def __str__(self) -> str:
        return f"{self.user} {self.transaction_type} ${self.amount}"


class AccountRetentionPolicyOverride(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="account_retention_overrides")
    account_label = models.CharField(max_length=80)
    source_template = models.ForeignKey("AccountRetentionPolicyTemplate", null=True, blank=True, on_delete=models.SET_NULL, related_name="seeded_overrides")
    evidence_retention_default_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_verified_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_strong_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_weak_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_placeholder_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_confirmation_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_import_match_days = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "account_label"], name="uniq_account_retention_override"),
        ]
        indexes = [
            models.Index(fields=["user", "account_label"], name="idx_retention_override_account"),
        ]
        ordering = ("account_label",)

    def __str__(self) -> str:
        return f"{self.user} {self.account_label} retention override"


class AccountRetentionPolicyTemplate(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="account_retention_templates")
    family_label = models.CharField(max_length=80, blank=True, default="")
    template_name = models.CharField(max_length=100)
    notes = models.TextField(blank=True)
    evidence_retention_default_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_verified_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_strong_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_weak_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_placeholder_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_confirmation_days = models.PositiveIntegerField(null=True, blank=True)
    evidence_retention_import_match_days = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "template_name"], name="uniq_account_retention_template_name"),
        ]
        indexes = [
            models.Index(fields=["user", "family_label", "template_name"], name="idx_retention_template_family"),
        ]
        ordering = ("family_label", "template_name")

    def __str__(self) -> str:
        family = f" [{self.family_label}]" if self.family_label else ""
        return f"{self.user} {self.template_name}{family} retention template"


class ImportedBrokerSnapshot(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="broker_snapshots")
    source_label = models.CharField(max_length=80, default="Broker CSV")
    account_label = models.CharField(max_length=80, blank=True, default="")
    as_of = models.DateTimeField(default=timezone.now)
    account_equity = models.DecimalField(max_digits=20, decimal_places=2)
    cash_balance = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-as_of"], name="idx_brokersnap_user_asof"),
        ]
        ordering = ("-as_of", "-id")

    def __str__(self) -> str:
        label = self.account_label or self.source_label
        return f"{self.user} {label} {self.as_of:%Y-%m-%d %H:%M}"


class BrokerPositionImportRun(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="broker_position_import_runs")
    source_label = models.CharField(max_length=80, default="Broker CSV")
    account_label = models.CharField(max_length=80, blank=True, default="")
    uploaded_filename = models.CharField(max_length=255, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    preview_rows = models.JSONField(default=list, blank=True)
    unresolved_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"], name="idx_brokerrun_user_recent"),
        ]
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        label = self.account_label or self.uploaded_filename or self.source_label
        return f"{self.user} {label} {self.created_at:%Y-%m-%d %H:%M}"


class BrokerPositionImportResolution(models.Model):
    class Action(models.TextChoices):
        REVIEWED_OK = "REVIEWED_OK", "Reviewed and acceptable"
        CLOSE_TRACKED = "CLOSE_TRACKED", "Close tracked holding"
        ADD_TRACKED = "ADD_TRACKED", "Add tracked holding"
        QUANTITY_ACCEPTED = "QUANTITY_ACCEPTED", "Quantity difference accepted"
        FOLLOW_UP = "FOLLOW_UP", "Manual follow-up"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="broker_position_import_resolutions")
    run = models.ForeignKey(BrokerPositionImportRun, on_delete=models.CASCADE, related_name="resolutions")
    symbol = models.CharField(max_length=32)
    tracked_position = models.ForeignKey("HeldPosition", null=True, blank=True, on_delete=models.SET_NULL, related_name="broker_import_resolutions")
    action = models.CharField(max_length=24, choices=Action.choices)
    note = models.TextField(blank=True)
    resolved_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("run", "symbol")
        indexes = [
            models.Index(fields=["run", "symbol"], name="idx_brokerres_run_symbol"),
            models.Index(fields=["user", "-resolved_at"], name="idx_brokerres_user_recent"),
        ]
        ordering = ("-resolved_at", "-id")

    def __str__(self) -> str:
        return f"{self.run_id} {self.symbol} {self.action}"


class SavedFilterPreset(models.Model):
    class Scope(models.TextChoices):
        SIGNALS = "SIGNALS", "Signals"
        HOLDINGS = "HOLDINGS", "Holdings"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_filter_presets")
    scope = models.CharField(max_length=16, choices=Scope.choices)
    name = models.CharField(max_length=100)
    filters = models.JSONField(default=dict, blank=True)
    is_dashboard_widget = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "scope", "name")
        indexes = [
            models.Index(fields=["user", "scope", "name"], name="idx_filterpreset_scope"),
        ]
        ordering = ("scope", "name")

    def __str__(self) -> str:
        return f"{self.user} {self.scope} {self.name}"


class HeldPosition(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "Manual entry"
        IMPORT = "IMPORT", "Import"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="held_positions")
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="held_positions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)

    quantity = models.DecimalField(max_digits=20, decimal_places=8)
    average_entry_price = models.DecimalField(max_digits=20, decimal_places=8)
    opened_at = models.DateTimeField(default=timezone.now)

    stop_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    target_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)

    thesis = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    last_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    last_price_at = models.DateTimeField(null=True, blank=True)
    pnl_amount = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    pnl_pct = models.FloatField(null=True, blank=True)

    close_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_notes = models.TextField(blank=True)

    account_label = models.CharField(max_length=80, blank=True, default="")

    last_import_seen_at = models.DateTimeField(null=True, blank=True)
    missing_from_latest_import = models.BooleanField(default=False)
    reconciliation_note = models.TextField(blank=True)
    reconciliation_resolved_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status", "-updated_at"], name="idx_hold_user_status"),
            models.Index(fields=["instrument", "status"], name="idx_hold_inst_status"),
            models.Index(fields=["user", "status", "missing_from_latest_import"], name="idx_hold_import_gap"),
            models.Index(fields=["user", "account_label", "status"], name="idx_hold_user_account"),
        ]

    def __str__(self) -> str:
        label = f" [{self.account_label}]" if self.account_label else ""
        return f"{self.user} {self.instrument.symbol}{label} {self.quantity} {self.status}"


class HoldingAlert(models.Model):
    class AlertType(models.TextChoices):
        STOP_BREACH = "STOP_BREACH", "Stop breach"
        THESIS_BREAK = "THESIS_BREAK", "Thesis break"
        DETERIORATING = "DETERIORATING", "Deteriorating"
        TARGET_REACHED = "TARGET_REACHED", "Target reached"

    class Channel(models.TextChoices):
        DISCORD = "DISCORD", "Discord"
        EMAIL = "EMAIL", "Email"

    class Status(models.TextChoices):
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        SKIPPED = "SKIPPED", "Skipped"
        DRY_RUN = "DRY_RUN", "Dry run"

    position = models.ForeignKey(HeldPosition, on_delete=models.CASCADE, related_name="alerts")
    alert_type = models.CharField(max_length=24, choices=AlertType.choices)
    channel = models.CharField(max_length=16, choices=Channel.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    reason = models.CharField(max_length=64, blank=True)
    message = models.TextField(blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    payload_snapshot = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["position", "alert_type", "channel", "-created_at"], name="idx_holdalert_recent"),
        ]

    def __str__(self) -> str:
        return f"{self.position.instrument.symbol} {self.alert_type} {self.channel} {self.status}"


class HoldingTransaction(models.Model):
    class EventType(models.TextChoices):
        OPEN = "OPEN", "Open"
        IMPORT_SYNC = "IMPORT_SYNC", "Import sync"
        BUY_ADD = "BUY_ADD", "Add shares"
        PARTIAL_SELL = "PARTIAL_SELL", "Partial sell"
        CLOSE = "CLOSE", "Close"
        ACCOUNT_TRANSFER = "ACCOUNT_TRANSFER", "Move / relabel account"

    class StopPolicyReasonCode(models.TextChoices):
        WAITING_CONFIRMATION = "WAITING_CONFIRMATION", "Waiting for confirmation"
        BROKER_OR_IMPORT_DELAY = "BROKER_OR_IMPORT_DELAY", "Broker or import delay"
        INTENTIONAL_DEFER = "INTENTIONAL_DEFER", "Intentional defer"
        EXISTING_PLAN_OUTSIDE_APP = "EXISTING_PLAN_OUTSIDE_APP", "Existing stop plan outside app"
        SCALING_EXCEPTION = "SCALING_EXCEPTION", "Scaling / staging exception"
        MANUAL_REVIEW = "MANUAL_REVIEW", "Manual review needed"
        OTHER = "OTHER", "Other"

    class ExecutionEvidenceType(models.TextChoices):
        BROKER_CONFIRMATION = "BROKER_CONFIRMATION", "Broker confirmation"
        ORDER_REFERENCE = "ORDER_REFERENCE", "Order reference / fill ID"
        IMPORT_MATCH = "IMPORT_MATCH", "Later broker/import match"
        MANUAL_VERIFICATION = "MANUAL_VERIFICATION", "Manual verification"

    class ExecutionEvidenceQuality(models.TextChoices):
        VERIFIED = "VERIFIED", "Verified"
        STRONG = "STRONG", "Strong"
        WEAK = "WEAK", "Weak"
        PLACEHOLDER = "PLACEHOLDER", "Placeholder / unverified"

    position = models.ForeignKey(HeldPosition, on_delete=models.CASCADE, related_name="transactions")
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    quantity = models.DecimalField(max_digits=20, decimal_places=8)
    price = models.DecimalField(max_digits=20, decimal_places=8)
    account_label_snapshot = models.CharField(max_length=80, blank=True, default="")
    stop_price_snapshot = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    risk_guardrail_posture_snapshot = models.CharField(max_length=16, blank=True, default="")
    risk_guardrail_reason_snapshot = models.CharField(max_length=120, blank=True, default="")
    stop_policy_due_at = models.DateTimeField(null=True, blank=True)
    stop_policy_resolved_at = models.DateTimeField(null=True, blank=True)
    stop_policy_status = models.CharField(max_length=16, blank=True, default="")
    stop_policy_reason_code = models.CharField(max_length=32, blank=True, default="")
    stop_policy_note = models.TextField(blank=True)
    execution_evidence_type = models.CharField(max_length=32, blank=True, default="")
    execution_evidence_quality = models.CharField(max_length=20, blank=True, default="")
    execution_evidence_reference = models.CharField(max_length=120, blank=True, default="")
    execution_evidence_note = models.TextField(blank=True)
    execution_evidence_recorded_at = models.DateTimeField(null=True, blank=True)
    execution_evidence_attachment = models.FileField(upload_to=holding_transaction_evidence_upload_to, blank=True, null=True)
    execution_evidence_retention_until = models.DateTimeField(null=True, blank=True)
    broker_confirmation_snapshot = models.ForeignKey("ImportedBrokerSnapshot", null=True, blank=True, on_delete=models.SET_NULL, related_name="linked_transactions")
    broker_confirmation_run = models.ForeignKey("BrokerPositionImportRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="linked_transactions")
    broker_confirmation_resolution = models.ForeignKey("BrokerPositionImportResolution", null=True, blank=True, on_delete=models.SET_NULL, related_name="linked_transactions")
    broker_confirmation_linked_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    realized_pnl_amount = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["position", "-created_at"], name="idx_holdtxn_recent"),
        ]
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.position.instrument.symbol} {self.event_type} {self.quantity}@{self.price}"




class PortfolioHealthSnapshot(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="portfolio_health_snapshots")
    overall_score = models.PositiveIntegerField(default=100)
    overall_grade_code = models.CharField(max_length=16, blank=True, default="")
    overall_grade_label = models.CharField(max_length=32, blank=True, default="")
    attention_count = models.PositiveIntegerField(default=0)
    urgent_count = models.PositiveIntegerField(default=0)
    weakest_account_label = models.CharField(max_length=80, blank=True, default="")
    weakest_account_score = models.PositiveIntegerField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"], name="idx_port_health_recent"),
        ]
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.user} health {self.overall_score} {self.created_at:%Y-%m-%d %H:%M}"


class EvidenceLifecycleAutomationRun(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="evidence_lifecycle_runs")
    archive_expired = models.BooleanField(default=False)
    scanned_count = models.PositiveIntegerField(default=0)
    attachment_count = models.PositiveIntegerField(default=0)
    expiring_soon_count = models.PositiveIntegerField(default=0)
    expired_count = models.PositiveIntegerField(default=0)
    missing_retention_count = models.PositiveIntegerField(default=0)
    archived_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"], name="idx_ev_lifecycle_recent"),
        ]
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        mode = "archive" if self.archive_expired else "scan"
        return f"{self.user} evidence lifecycle {mode} {self.created_at:%Y-%m-%d %H:%M}"
