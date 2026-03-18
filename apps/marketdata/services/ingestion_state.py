"""Per-symbol ingestion state backed by the IngestionState DB model.

Replaces the previous .runtime/ingestion_state.json implementation so that
cooldowns and unsupported-symbol flags survive process restarts and work on
ephemeral filesystems (e.g. Render free tier).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apps.marketdata.models import IngestionState


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Unsupported crypto symbols
# ---------------------------------------------------------------------------

_UNSUPPORTED_PREFIX = "unsupported:"


def mark_unsupported_crypto_symbol(symbol: str, reason: str) -> None:
    sym = (symbol or "").strip().upper()
    if not sym:
        return
    IngestionState.objects.update_or_create(
        key=f"{_UNSUPPORTED_PREFIX}{sym}",
        defaults={"reason": reason, "cooldown_until": None},
    )


def get_unsupported_crypto_reason(symbol: str) -> str | None:
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    try:
        row = IngestionState.objects.get(key=f"{_UNSUPPORTED_PREFIX}{sym}")
        return str(row.reason or "unsupported_crypto_pair")
    except IngestionState.DoesNotExist:
        return None


# ---------------------------------------------------------------------------
# Provider cooldowns
# ---------------------------------------------------------------------------

_COOLDOWN_PREFIX = "cooldown:"


def mark_provider_cooldown(symbol: str, provider_name: str | None, *, ttl_seconds: int, reason: str) -> None:
    sym = (symbol or "").strip().upper()
    provider = (provider_name or "").strip().lower() or "auto"
    if not sym:
        return
    until = _now() + timedelta(seconds=max(int(ttl_seconds), 1))
    IngestionState.objects.update_or_create(
        key=f"{_COOLDOWN_PREFIX}{sym}:{provider}",
        defaults={"reason": reason, "cooldown_until": until},
    )


def active_provider_cooldown_reason(symbol: str, provider_name: str | None) -> str | None:
    sym = (symbol or "").strip().upper()
    provider = (provider_name or "").strip().lower() or "auto"
    if not sym:
        return None
    try:
        row = IngestionState.objects.get(key=f"{_COOLDOWN_PREFIX}{sym}:{provider}")
    except IngestionState.DoesNotExist:
        return None
    if row.cooldown_until is None or row.cooldown_until <= _now():
        # Lazily expire
        row.delete()
        return None
    return str(row.reason or "provider_cooldown")
