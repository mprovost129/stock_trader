from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max, Min
from django.utils import timezone

from apps.marketdata.models import Instrument, PriceBar, ProviderHealthCheck
from apps.marketdata.services.health import provider_healthcheck
from apps.portfolios.models import InstrumentSelection, Watchlist


DEFAULT_STALE_THRESHOLDS_MINUTES = {
    "1m": 30,
    "5m": 180,
    "1d": 2880,
}
PROVIDER_HEALTH_CACHE_SECONDS = 900
PROVIDER_HISTORY_LIMIT = 5


@dataclass(frozen=True)
class CoverageRow:
    symbol: str
    asset_class: str
    latest_ts: object | None
    age_minutes: int | None
    is_stale: bool
    bar_count: int


@dataclass(frozen=True)
class ProviderStatusRow:
    provider: str
    label: str
    asset_class: str
    ok: bool
    message: str
    checked_at: object
    history_ok_count: int
    history_fail_count: int
    latest_streak: int
    success_rate_pct: float


def stale_threshold_minutes(timeframe: str) -> int:
    key = f"DATA_STALE_THRESHOLD_MINUTES_{str(timeframe or '').replace('m', 'M').replace('d', 'D')}"
    if timeframe == "1m":
        key = "DATA_STALE_THRESHOLD_MINUTES_1M"
    elif timeframe == "5m":
        key = "DATA_STALE_THRESHOLD_MINUTES_5M"
    elif timeframe == "1d":
        key = "DATA_STALE_THRESHOLD_MINUTES_1D"
    return int(getattr(settings, key, DEFAULT_STALE_THRESHOLDS_MINUTES.get(timeframe, 2880)) or DEFAULT_STALE_THRESHOLDS_MINUTES.get(timeframe, 2880))


def build_provider_health_summary(*, asset_class: str | None = None) -> dict:
    provider_map = [
        ("yahoo", "Yahoo", "STOCK"),
        ("polygon", "Polygon", "STOCK"),
        ("coinbase", "Coinbase", "CRYPTO"),
        ("kraken", "Kraken", "CRYPTO"),
        ("binance", "Binance", "CRYPTO"),
    ]
    relevant = [row for row in provider_map if not asset_class or row[2] == asset_class]
    rows: list[ProviderStatusRow] = []
    for provider, label, kind in relevant:
        rows.append(_cached_provider_status(provider=provider, label=label, asset_class=kind))

    ok_count = sum(1 for row in rows if row.ok)
    failure_count = len(rows) - ok_count
    banner = {
        "level": "success",
        "title": "Provider checks look healthy",
        "message": f"{ok_count} of {len(rows)} relevant provider checks passed in the cached health window.",
    }
    if not rows:
        banner = {
            "level": "secondary",
            "title": "No providers in scope",
            "message": "Change the dashboard scope to evaluate provider health for stocks or crypto.",
        }
    elif failure_count:
        banner = {
            "level": "warning" if ok_count else "danger",
            "title": "One or more provider checks failed",
            "message": f"{failure_count} provider checks failed in the current cached window. Run provider_healthcheck from the CLI for a fresh probe.",
        }

    checked_values = [row.checked_at for row in rows if row.checked_at]
    history_count = ProviderHealthCheck.objects.count()
    oldest_checked_at = ProviderHealthCheck.objects.aggregate(oldest=Min("checked_at")).get("oldest")
    retention_days = int(getattr(settings, "PROVIDER_HEALTH_RETENTION_DAYS", 30) or 30)
    auto_prune_every = int(getattr(settings, "SCHEDULER_PRUNE_PROVIDER_HEALTH_EVERY", 0) or 0)
    auto_prune_days = int(getattr(settings, "SCHEDULER_PRUNE_PROVIDER_HEALTH_DAYS", 0) or 0) or retention_days
    return {
        "rows": rows,
        "ok_count": ok_count,
        "failure_count": failure_count,
        "checked_at": max(checked_values) if checked_values else None,
        "cache_seconds": PROVIDER_HEALTH_CACHE_SECONDS,
        "banner": banner,
        "history_limit": PROVIDER_HISTORY_LIMIT,
        "history_count": history_count,
        "oldest_checked_at": oldest_checked_at,
        "retention_days": retention_days,
        "auto_prune_every": auto_prune_every,
        "auto_prune_days": auto_prune_days,
    }


def _cached_provider_status(*, provider: str, label: str, asset_class: str) -> ProviderStatusRow:
    cache_key = f"dashboard_provider_health:{provider}"
    cached = cache.get(cache_key)
    checked_at = None
    if isinstance(cached, dict):
        checked_at = cached.get("checked_at")
        if checked_at and (timezone.now() - checked_at).total_seconds() < PROVIDER_HEALTH_CACHE_SECONDS:
            return _build_provider_status_row(
                provider=provider,
                label=label,
                asset_class=asset_class,
                ok=bool(cached.get("ok")),
                message=str(cached.get("message") or "cached"),
                checked_at=checked_at,
            )

    result = provider_healthcheck(provider=provider)
    checked_at = timezone.now()
    ProviderHealthCheck.objects.create(
        provider=provider,
        asset_class=asset_class,
        ok=result.ok,
        message=(result.message or "")[:255],
        checked_at=checked_at,
    )
    payload = {
        "ok": result.ok,
        "message": result.message,
        "checked_at": checked_at,
    }
    cache.set(cache_key, payload, PROVIDER_HEALTH_CACHE_SECONDS)
    return _build_provider_status_row(
        provider=provider,
        label=label,
        asset_class=asset_class,
        ok=result.ok,
        message=result.message,
        checked_at=checked_at,
    )


def _build_provider_status_row(*, provider: str, label: str, asset_class: str, ok: bool, message: str, checked_at: object) -> ProviderStatusRow:
    recent_checks = list(
        ProviderHealthCheck.objects.filter(provider=provider).order_by("-checked_at")[:PROVIDER_HISTORY_LIMIT]
    )
    if not recent_checks:
        history_ok_count = 1 if ok else 0
        history_fail_count = 0 if ok else 1
        latest_streak = 1
    else:
        history_ok_count = sum(1 for item in recent_checks if item.ok)
        history_fail_count = len(recent_checks) - history_ok_count
        latest_state = recent_checks[0].ok
        latest_streak = 0
        for item in recent_checks:
            if item.ok != latest_state:
                break
            latest_streak += 1
    success_rate_pct = round((history_ok_count / max(len(recent_checks), 1)) * 100, 2)
    return ProviderStatusRow(
        provider=provider,
        label=label,
        asset_class=asset_class,
        ok=ok,
        message=message,
        checked_at=checked_at,
        history_ok_count=history_ok_count,
        history_fail_count=history_fail_count,
        latest_streak=latest_streak,
        success_rate_pct=success_rate_pct,
    )


def build_watchlist_health_summary(*, watchlist: Watchlist | None, timeframe: str = "1d", asset_class: str | None = None, top_n: int = 8) -> dict:
    now = timezone.now()
    threshold_minutes = stale_threshold_minutes(timeframe)
    threshold_delta = timedelta(minutes=threshold_minutes)

    if not watchlist:
        return {
            "selected_count": 0,
            "ready_count": 0,
            "missing_count": 0,
            "stale_count": 0,
            "fresh_count": 0,
            "stale_threshold_minutes": threshold_minutes,
            "oldest_latest_ts": None,
            "freshest_latest_ts": None,
            "rows": [],
            "stale_rows": [],
            "banner": {
                "level": "secondary",
                "title": "No watchlist loaded",
                "message": "Create or activate a watchlist before using data freshness warnings.",
            },
        }

    selections = InstrumentSelection.objects.select_related("instrument").filter(
        watchlist=watchlist,
        is_active=True,
        instrument__is_active=True,
    )
    if asset_class:
        selections = selections.filter(instrument__asset_class=asset_class)

    selection_rows = list(selections.order_by("instrument__symbol"))
    selected_count = len(selection_rows)
    if not selected_count:
        asset_label = dict(Instrument.AssetClass.choices).get(asset_class, "current scope") if asset_class else "watchlist"
        return {
            "selected_count": 0,
            "ready_count": 0,
            "missing_count": 0,
            "stale_count": 0,
            "fresh_count": 0,
            "stale_threshold_minutes": threshold_minutes,
            "oldest_latest_ts": None,
            "freshest_latest_ts": None,
            "rows": [],
            "stale_rows": [],
            "banner": {
                "level": "secondary",
                "title": "No symbols in scope",
                "message": f"No active {asset_label} symbols are currently selected in this watchlist scope.",
            },
        }

    instrument_ids = [item.instrument_id for item in selection_rows]
    bar_stats = {
        row["instrument_id"]: row
        for row in PriceBar.objects.filter(instrument_id__in=instrument_ids, timeframe=timeframe)
        .values("instrument_id")
        .annotate(latest_ts=Max("ts"), bar_count=Count("id"))
    }

    rows: list[CoverageRow] = []
    for selection in selection_rows:
        instrument = selection.instrument
        stats = bar_stats.get(selection.instrument_id, {})
        latest_ts = stats.get("latest_ts")
        age_minutes = None
        is_stale = False
        if latest_ts:
            age_minutes = max(0, int((now - latest_ts).total_seconds() // 60))
            is_stale = (now - latest_ts) > threshold_delta
        rows.append(
            CoverageRow(
                symbol=instrument.symbol,
                asset_class=instrument.asset_class,
                latest_ts=latest_ts,
                age_minutes=age_minutes,
                is_stale=is_stale,
                bar_count=int(stats.get("bar_count") or 0),
            )
        )

    ready_rows = [row for row in rows if row.latest_ts]
    stale_rows = [row for row in ready_rows if row.is_stale]
    fresh_rows = [row for row in ready_rows if not row.is_stale]
    missing_count = selected_count - len(ready_rows)
    stale_ratio = (len(stale_rows) / selected_count) if selected_count else 0

    banner = {
        "level": "success",
        "title": "Coverage looks healthy",
        "message": f"{len(fresh_rows)} of {selected_count} symbols are within the freshness target for {timeframe}.",
    }
    if missing_count == selected_count:
        banner = {
            "level": "danger",
            "title": "No bars loaded for this scope",
            "message": f"None of the {selected_count} symbols in scope have {timeframe} bars yet. Run a watchlist ingest or backfill before trusting scans.",
        }
    elif missing_count or stale_rows:
        if stale_ratio >= 0.5 or missing_count >= max(3, selected_count // 3):
            level = "danger"
        else:
            level = "warning"
        banner = {
            "level": level,
            "title": "Coverage needs attention",
            "message": (
                f"Missing: {missing_count}. Stale: {len(stale_rows)}. "
                f"Anything older than {threshold_minutes} minutes is treated as stale for {timeframe}."
            ),
        }

    rows_sorted = sorted(
        rows,
        key=lambda item: (
            0 if item.latest_ts is None else 1,
            -(item.age_minutes or 0),
            item.symbol,
        ),
    )
    stale_rows_sorted = [row for row in rows_sorted if row.latest_ts is None or row.is_stale][:top_n]

    latest_values = [row.latest_ts for row in ready_rows if row.latest_ts]
    return {
        "selected_count": selected_count,
        "ready_count": len(ready_rows),
        "missing_count": missing_count,
        "stale_count": len(stale_rows),
        "fresh_count": len(fresh_rows),
        "stale_threshold_minutes": threshold_minutes,
        "oldest_latest_ts": min(latest_values) if latest_values else None,
        "freshest_latest_ts": max(latest_values) if latest_values else None,
        "rows": rows_sorted,
        "stale_rows": stale_rows_sorted,
        "banner": banner,
    }
