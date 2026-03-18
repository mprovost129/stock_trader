from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.signals.models import Signal


class JournalEntry(models.Model):
    class Decision(models.TextChoices):
        YES = "YES", "Yes (took it)"
        NO = "NO", "No (passed)"
        SKIP = "SKIP", "Skip/Other"

    class Outcome(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Unknown"
        WIN = "WIN", "Win"
        LOSS = "LOSS", "Loss"
        BREAKEVEN = "BREAKEVEN", "Breakeven"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    signal = models.ForeignKey(Signal, on_delete=models.SET_NULL, null=True, blank=True, related_name="journal_entries")

    decided_at = models.DateTimeField(default=timezone.now)
    decision = models.CharField(max_length=8, choices=Decision.choices)

    notes = models.TextField(blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, default=Outcome.UNKNOWN)
    realized_r = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Optional: realized R-multiple")
    tags = models.CharField(max_length=255, blank=True, help_text="Comma-separated tags")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-decided_at",)

    def __str__(self) -> str:
        return f"{self.user} {self.decision} {self.decided_at:%Y-%m-%d}"
