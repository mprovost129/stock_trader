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


def clear_unsupported_crypto_symbols(symbols: list[str] | tuple[str, ...] | set[str] | None = None) -> int:
    """Remove persisted unsupported-crypto flags.

    If symbols are provided, clears only those symbol keys. Otherwise clears all.
    Returns number of deleted rows.
    """
    if symbols:
        normalized = [str(sym).strip().upper() for sym in symbols if str(sym).strip()]
        if not normalized:
            return 0
        keys = [f"{_UNSUPPORTED_PREFIX}{sym}" for sym in normalized]
        deleted, _ = IngestionState.objects.filter(key__in=keys).delete()
        return int(deleted)
    deleted, _ = IngestionState.objects.filter(key__startswith=_UNSUPPORTED_PREFIX).delete()
    return int(deleted)


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


def clear_provider_cooldowns(symbols: list[str] | tuple[str, ...] | set[str] | None = None) -> int:
    """Remove persisted provider-cooldown flags.

    If symbols are provided, clears only those symbol cooldown keys across providers.
    Otherwise clears all cooldown keys.
    Returns number of deleted rows.
    """
    if symbols:
        normalized = [str(sym).strip().upper() for sym in symbols if str(sym).strip()]
        if not normalized:
            return 0
        deleted, _ = IngestionState.objects.filter(
            key__startswith=_COOLDOWN_PREFIX,
            key__in=[
                f"{_COOLDOWN_PREFIX}{sym}:auto" for sym in normalized
            ] + [
                f"{_COOLDOWN_PREFIX}{sym}:coinbase" for sym in normalized
            ] + [
                f"{_COOLDOWN_PREFIX}{sym}:kraken" for sym in normalized
            ] + [
                f"{_COOLDOWN_PREFIX}{sym}:binance" for sym in normalized
            ],
        ).delete()
        return int(deleted)
    deleted, _ = IngestionState.objects.filter(key__startswith=_COOLDOWN_PREFIX).delete()
    return int(deleted)
