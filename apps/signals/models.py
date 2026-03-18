from django.db import models
from django.conf import settings
from django.utils import timezone

from apps.marketdata.models import Instrument
from apps.strategies.models import Strategy


class Signal(models.Model):
    class Direction(models.TextChoices):
        LONG = "LONG", "Long"
        SHORT = "SHORT", "Short"
        FLAT = "FLAT", "Flat"

    class Status(models.TextChoices):
        NEW = "NEW", "New"
        REVIEWED = "REVIEWED", "Reviewed"
        TAKEN = "TAKEN", "Taken"
        SKIPPED = "SKIPPED", "Skipped"
        CLOSED_WIN = "CLOSED_WIN", "Closed win"
        CLOSED_LOSS = "CLOSED_LOSS", "Closed loss"
        CONFIRMED = "CONFIRMED", "Confirmed"
        REJECTED = "REJECTED", "Rejected"
        EXPIRED = "EXPIRED", "Expired"
        ARCHIVED = "ARCHIVED", "Archived"

    class SignalKind(models.TextChoices):
        EVENT = "EVENT", "Event"
        STATE = "STATE", "State"

    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="signals")
    strategy = models.ForeignKey(Strategy, on_delete=models.CASCADE, related_name="signals")
    timeframe = models.CharField(max_length=8, default="1m")

    generated_at = models.DateTimeField(default=timezone.now)
    direction = models.CharField(max_length=8, choices=Direction.choices)
    signal_kind = models.CharField(max_length=16, choices=SignalKind.choices, default=SignalKind.EVENT)
    signal_label = models.CharField(max_length=32, blank=True)
    score = models.FloatField(null=True, blank=True)
    score_components = models.JSONField(default=dict, blank=True)
    rationale = models.TextField(blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="signals_created")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["-generated_at"], name="idx_signal_generated_at"),
            models.Index(fields=["instrument", "strategy"], name="idx_signal_inst_strategy"),
            models.Index(fields=["signal_kind", "signal_label"], name="idx_signal_kind_label"),
        ]

    def __str__(self) -> str:
        label = f" {self.signal_label}" if self.signal_label else ""
        return f"{self.instrument.symbol} {self.strategy.slug} {self.timeframe} {self.direction}{label}"


class TradePlan(models.Model):
    signal = models.OneToOneField(Signal, on_delete=models.CASCADE, related_name="trade_plan")

    entry_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    stop_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    target_1 = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    target_2 = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)

    account_equity = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    risk_per_trade_pct = models.DecimalField(max_digits=6, decimal_places=4, default=0.0025)  # 0.25%
    suggested_qty = models.IntegerField(null=True, blank=True)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"TradePlan for {self.signal_id}"


class AlertDelivery(models.Model):
    class Channel(models.TextChoices):
        DISCORD = "DISCORD", "Discord"
        EMAIL = "EMAIL", "Email"

    class Status(models.TextChoices):
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        SKIPPED = "SKIPPED", "Skipped"
        DRY_RUN = "DRY_RUN", "Dry run"

    signal = models.ForeignKey(Signal, on_delete=models.CASCADE, related_name="alert_deliveries")
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.DISCORD)
    status = models.CharField(max_length=16, choices=Status.choices)
    reason = models.CharField(max_length=64, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    payload_snapshot = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["channel", "status", "-created_at"], name="idx_alert_chan_stat_ct"),
        ]

    def __str__(self) -> str:
        return f"{self.signal.instrument.symbol} {self.channel} {self.status}"


class OperatorNotification(models.Model):
    class Kind(models.TextChoices):
        DELIVERY_HEALTH = "DELIVERY_HEALTH", "Delivery health"
        DELIVERY_RECOVERY = "DELIVERY_RECOVERY", "Delivery recovery"
        PORTFOLIO_HEALTH = "PORTFOLIO_HEALTH", "Portfolio health"

    class Channel(models.TextChoices):
        DISCORD = "DISCORD", "Discord"
        EMAIL = "EMAIL", "Email"

    class Status(models.TextChoices):
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        SKIPPED = "SKIPPED", "Skipped"
        DRY_RUN = "DRY_RUN", "Dry run"

    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.DELIVERY_HEALTH)
    channel = models.CharField(max_length=16, choices=Channel.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    reason = models.CharField(max_length=64, blank=True)
    headline = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    payload_snapshot = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["kind", "channel", "status", "-created_at"], name="idx_opnotif_kind_ct"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.channel} {self.status}"


class SignalOutcome(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        EVALUATED = "EVALUATED", "Evaluated"
        INSUFFICIENT = "INSUFFICIENT", "Insufficient bars"

    class OutcomeLabel(models.TextChoices):
        WIN = "WIN", "Win"
        LOSS = "LOSS", "Loss"
        MIXED = "MIXED", "Mixed"
        OPEN = "OPEN", "Open"
        AMBIGUOUS = "AMBIGUOUS", "Ambiguous"
        FLAT = "FLAT", "Flat"

    signal = models.OneToOneField(Signal, on_delete=models.CASCADE, related_name="outcome")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    outcome_label = models.CharField(max_length=16, choices=OutcomeLabel.choices, blank=True)
    lookahead_bars = models.PositiveIntegerField(default=5)
    bars_observed = models.PositiveIntegerField(default=0)

    reference_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    return_pct = models.FloatField(null=True, blank=True)
    max_favorable_excursion_pct = models.FloatField(null=True, blank=True)
    max_adverse_excursion_pct = models.FloatField(null=True, blank=True)

    target_1_hit = models.BooleanField(default=False)
    target_2_hit = models.BooleanField(default=False)
    stop_hit = models.BooleanField(default=False)

    evaluation_notes = models.TextField(blank=True)
    evaluated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "-updated_at"], name="idx_sigout_status_upd"),
            models.Index(fields=["outcome_label", "-evaluated_at"], name="idx_sigout_label_eval"),
        ]

    def __str__(self) -> str:
        return f"Outcome for {self.signal.instrument.symbol} {self.status}"


class PaperTrade(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    class LifecycleStage(models.TextChoices):
        NEW = "NEW", "New"
        ACTIVE = "ACTIVE", "Active"
        TARGET_1 = "TARGET_1", "Target 1 hit"
        TARGET_2 = "TARGET_2", "Target 2 hit"
        STOP_RISK = "STOP_RISK", "Stop risk"
        EXIT_READY = "EXIT_READY", "Exit ready"
        CLOSED = "CLOSED", "Closed"

    class ClosedReason(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        STOP = "STOP", "Stop"
        TARGET_2 = "TARGET_2", "Target 2"
        REVERSAL = "REVERSAL", "Reversal"
        OTHER = "OTHER", "Other"

    signal = models.OneToOneField(Signal, on_delete=models.CASCADE, related_name="paper_trade")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="paper_trades")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    lifecycle_stage = models.CharField(max_length=16, choices=LifecycleStage.choices, default=LifecycleStage.NEW)

    entry_price = models.DecimalField(max_digits=20, decimal_places=8)
    entry_time = models.DateTimeField(default=timezone.now)
    exit_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    exit_time = models.DateTimeField(null=True, blank=True)
    quantity = models.IntegerField(null=True, blank=True)
    risk_amount = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    pnl_amount = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    pnl_pct = models.FloatField(null=True, blank=True)
    target_1_hit = models.BooleanField(default=False)
    target_2_hit = models.BooleanField(default=False)
    stop_triggered = models.BooleanField(default=False)
    active_stop_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    active_target_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    trailing_stop_pct = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    highest_price_seen = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    lowest_price_seen = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    last_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    last_price_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=16, choices=ClosedReason.choices, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "-updated_at"], name="idx_ptrade_status_upd"),
        ]

    def __str__(self) -> str:
        return f"PaperTrade {self.signal.instrument.symbol} {self.status}"


class PositionAlert(models.Model):
    class AlertType(models.TextChoices):
        DETERIORATING = "DETERIORATING", "Deteriorating"
        STOP_APPROACHING = "STOP_APPROACHING", "Stop approaching"
        TREND_REVERSAL = "TREND_REVERSAL", "Trend reversal"

    class Status(models.TextChoices):
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        SKIPPED = "SKIPPED", "Skipped"
        DRY_RUN = "DRY_RUN", "Dry run"

    paper_trade = models.ForeignKey(PaperTrade, on_delete=models.CASCADE, related_name="position_alerts")
    alert_type = models.CharField(max_length=32, choices=AlertType.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    reason = models.CharField(max_length=64, blank=True)
    payload_snapshot = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["alert_type", "status", "-created_at"], name="idx_posalert_type_stat_ct"),
        ]

    def __str__(self) -> str:
        return f"{self.paper_trade.signal.instrument.symbol} {self.alert_type} {self.status}"
