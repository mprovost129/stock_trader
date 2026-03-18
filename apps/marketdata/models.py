from django.db import models


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
