from django.db import models
from django.conf import settings
from django.utils import timezone


class Instrument(models.Model):
    class AssetClass(models.TextChoices):
        STOCK = "STOCK", "Stock"
        CRYPTO = "CRYPTO", "Crypto"

    symbol = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, blank=True)
    asset_class = models.CharField(max_length=16, choices=AssetClass.choices)
    exchange = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.symbol


class PriceBar(models.Model):
    class Timeframe(models.TextChoices):
        M1 = "1m", "1m"
        M5 = "5m", "5m"
        D1 = "1d", "1d"

    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="price_bars")
    timeframe = models.CharField(max_length=4, choices=Timeframe.choices)
    ts = models.DateTimeField(help_text="Bar timestamp (end time) in UTC")

    open = models.DecimalField(max_digits=20, decimal_places=8)
    high = models.DecimalField(max_digits=20, decimal_places=8)
    low = models.DecimalField(max_digits=20, decimal_places=8)
    close = models.DecimalField(max_digits=20, decimal_places=8)
    volume = models.DecimalField(max_digits=24, decimal_places=8)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["instrument", "timeframe", "ts"], name="uniq_bar_instrument_tf_ts"),
        ]
        indexes = [
            models.Index(fields=["instrument", "timeframe", "-ts"], name="idx_bar_instrument_tf_ts"),
        ]

    def __str__(self) -> str:
        return f"{self.instrument.symbol} {self.timeframe} {self.ts.isoformat()}"


class IngestionState(models.Model):
    """Tracks per-symbol ingestion state (cooldowns, unsupported flags).

    Replaces the previous .runtime/ingestion_state.json file so that state
    survives process restarts and works correctly on ephemeral filesystems
    (e.g. Render free tier).
    """

    # Key is "{SYMBOL}:{provider}" for cooldowns; just "{SYMBOL}" for unsupported flags.
    key = models.CharField(max_length=64, unique=True)
    reason = models.CharField(max_length=255, blank=True)
    cooldown_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["key"], name="idx_ingestion_state_key"),
        ]

    def __str__(self) -> str:
        return self.key


class IngestionJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"

    class Source(models.TextChoices):
        DATA_FRESHNESS = "DATA_FRESHNESS", "Data freshness"
        OPERATOR = "OPERATOR", "Operator"
        MANUAL = "MANUAL", "Manual"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ingestion_jobs")
    watchlist_name = models.CharField(max_length=100, default="Default")
    source = models.CharField(max_length=24, choices=Source.choices, default=Source.MANUAL)

    asset_class = models.CharField(max_length=16, blank=True, default="")
    stock_timeframe = models.CharField(max_length=4, default="1d")
    crypto_timeframe = models.CharField(max_length=4, default="1d")
    stock_provider = models.CharField(max_length=32, blank=True, default="")
    crypto_provider = models.CharField(max_length=32, blank=True, default="")
    symbols_csv = models.TextField(blank=True, default="")
    limit = models.PositiveIntegerField(default=300)
    max_symbols = models.PositiveIntegerField(default=8)
    throttle_seconds = models.FloatField(default=1.0)

    run_after = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=1)

    last_error = models.TextField(blank=True, default="")
    result_summary = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "run_after"], name="idx_ingestjob_status_runafter"),
            models.Index(fields=["user", "-created_at"], name="idx_ingestjob_user_recent"),
        ]
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.user} {self.asset_class or 'ALL'} {self.status} {self.created_at:%Y-%m-%d %H:%M}"
