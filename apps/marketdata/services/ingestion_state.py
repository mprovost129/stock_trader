from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from django.conf import settings

RUNTIME_DIR = Path(settings.BASE_DIR) / '.runtime'
STATE_FILE = RUNTIME_DIR / 'ingestion_state.json'


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"unsupported_crypto": {}, "provider_cooldowns": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {"unsupported_crypto": {}, "provider_cooldowns": {}}


def _save_state(state: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding='utf-8')


def mark_unsupported_crypto_symbol(symbol: str, reason: str) -> None:
    sym = (symbol or '').strip().upper()
    if not sym:
        return
    state = _load_state()
    state.setdefault('unsupported_crypto', {})[sym] = {
        'reason': reason,
        'marked_at': _now().isoformat(),
    }
    _save_state(state)


def get_unsupported_crypto_reason(symbol: str) -> str | None:
    sym = (symbol or '').strip().upper()
    if not sym:
        return None
    state = _load_state()
    row = (state.get('unsupported_crypto') or {}).get(sym)
    if not row:
        return None
    return str(row.get('reason') or 'unsupported_crypto_pair')


def mark_provider_cooldown(symbol: str, provider_name: str | None, *, ttl_seconds: int, reason: str) -> None:
    sym = (symbol or '').strip().upper()
    provider = (provider_name or '').strip().lower() or 'auto'
    if not sym:
        return
    until = _now() + timedelta(seconds=max(int(ttl_seconds), 1))
    state = _load_state()
    state.setdefault('provider_cooldowns', {})[f'{sym}:{provider}'] = {
        'reason': reason,
        'until': until.isoformat(),
    }
    _save_state(state)


def active_provider_cooldown_reason(symbol: str, provider_name: str | None) -> str | None:
    sym = (symbol or '').strip().upper()
    provider = (provider_name or '').strip().lower() or 'auto'
    if not sym:
        return None
    state = _load_state()
    row = (state.get('provider_cooldowns') or {}).get(f'{sym}:{provider}')
    if not row:
        return None
    until_raw = row.get('until')
    try:
        until = datetime.fromisoformat(str(until_raw))
    except Exception:
        until = None
    if until is None or until.tzinfo is None:
        until = _now() - timedelta(seconds=1)
    if until <= _now():
        # expire stale entry lazily
        state.get('provider_cooldowns', {}).pop(f'{sym}:{provider}', None)
        _save_state(state)
        return None
    return str(row.get('reason') or 'provider_cooldown')
