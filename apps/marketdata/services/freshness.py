from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max
from django.utils import timezone

from apps.marketdata.models import Instrument, PriceBar
from apps.marketdata.services.crypto_router import INVALID_CRYPTO_SYMBOLS
from apps.marketdata.services.ingestion_state import active_provider_cooldown_reason, get_unsupported_crypto_reason
from apps.portfolios.models import InstrumentSelection, Watchlist


DEFAULT_STALE_THRESHOLDS_MINUTES = {
    "1m": 30,
    "5m": 180,
    "1d": 2880,
}


def stale_threshold_minutes(timeframe: str) -> int:
    value = (timeframe or "1d").strip().lower()
    env_map = {
        "1m": "DATA_STALE_THRESHOLD_MINUTES_1M",
        "5m": "DATA_STALE_THRESHOLD_MINUTES_5M",
        "1d": "DATA_STALE_THRESHOLD_MINUTES_1D",
    }
    env_key = env_map.get(value)
    fallback = DEFAULT_STALE_THRESHOLDS_MINUTES.get(value, 2880)
    if not env_key:
        return fallback
    return int(getattr(settings, env_key, fallback) or fallback)


def build_data_freshness_summary(*, watchlist: Watchlist | None, timeframe: str = "1d", top_n: int = 25) -> dict:
    if not watchlist:
        return {
            "watchlist": None,
            "timeframe": timeframe,
            "threshold_minutes": stale_threshold_minutes(timeframe),
            "selected_count": 0,
            "fresh_count": 0,
            "stale_count": 0,
            "missing_count": 0,
            "rows": [],
            "stale_or_missing_rows": [],
            "stock_rows": [],
            "crypto_rows": [],
            "crypto_diagnostics": [],
        }

    now = timezone.now()
    threshold_minutes = stale_threshold_minutes(timeframe)
    stale_delta = timedelta(minutes=threshold_minutes)
    selections = list(
        InstrumentSelection.objects.select_related("instrument")
        .filter(watchlist=watchlist, is_active=True, instrument__is_active=True)
        .order_by("instrument__asset_class", "instrument__symbol")
    )
    instrument_ids = [row.instrument_id for row in selections]

    bar_stats = {
        row["instrument_id"]: row
        for row in PriceBar.objects.filter(instrument_id__in=instrument_ids, timeframe=timeframe)
        .values("instrument_id")
        .annotate(latest_ts=Max("ts"), bar_count=Count("id"))
    }

    rows: list[dict] = []
    for selection in selections:
        instrument = selection.instrument
        stats = bar_stats.get(selection.instrument_id, {})
        latest_ts = stats.get("latest_ts")
        bar_count = int(stats.get("bar_count") or 0)
        age_minutes = None
        is_missing = latest_ts is None
        is_stale = False
        if latest_ts is not None:
            age_minutes = max(int((now - latest_ts).total_seconds() // 60), 0)
            is_stale = (now - latest_ts) > stale_delta
        rows.append(
            {
                "symbol": instrument.symbol,
                "name": instrument.name,
                "asset_class": instrument.asset_class,
                "priority": selection.priority,
                "sector": selection.sector,
                "latest_ts": latest_ts,
                "bar_count": bar_count,
                "age_minutes": age_minutes,
                "is_missing": is_missing,
                "is_stale": is_stale,
            }
        )

    rows.sort(
        key=lambda item: (
            0 if item["is_missing"] else 1,
            0 if item["is_stale"] else 1,
            -(item["age_minutes"] or 0),
            item["symbol"],
        )
    )

    fresh_count = sum(1 for row in rows if not row["is_missing"] and not row["is_stale"])
    stale_count = sum(1 for row in rows if row["is_stale"])
    missing_count = sum(1 for row in rows if row["is_missing"])

    crypto_rows = [row for row in rows if row["asset_class"] == Instrument.AssetClass.CRYPTO]
    stock_rows = [row for row in rows if row["asset_class"] == Instrument.AssetClass.STOCK]
    stale_or_missing_rows = [row for row in rows if row["is_missing"] or row["is_stale"]][: max(int(top_n), 1)]

    crypto_diagnostics: list[dict] = []
    for row in crypto_rows:
        symbol = row["symbol"]
        unsupported_reason = get_unsupported_crypto_reason(symbol)
        cooldown_reason = (
            active_provider_cooldown_reason(symbol, None)
            or active_provider_cooldown_reason(symbol, "coinbase")
            or active_provider_cooldown_reason(symbol, "kraken")
            or active_provider_cooldown_reason(symbol, "binance")
        )
        invalid_symbol = symbol in INVALID_CRYPTO_SYMBOLS or len(symbol) < 3
        if invalid_symbol:
            route_status = "invalid_symbol"
        elif unsupported_reason:
            route_status = "unsupported_pair"
        elif cooldown_reason:
            route_status = "provider_cooldown"
        else:
            route_status = "eligible_for_auto_route"
        crypto_diagnostics.append(
            {
                "symbol": symbol,
                "latest_ts": row["latest_ts"],
                "age_minutes": row["age_minutes"],
                "is_missing": row["is_missing"],
                "is_stale": row["is_stale"],
                "invalid_symbol": invalid_symbol,
                "unsupported_reason": unsupported_reason or "",
                "cooldown_reason": cooldown_reason or "",
                "route_status": route_status,
            }
        )

    return {
        "watchlist": watchlist,
        "timeframe": timeframe,
        "threshold_minutes": threshold_minutes,
        "selected_count": len(rows),
        "fresh_count": fresh_count,
        "stale_count": stale_count,
        "missing_count": missing_count,
        "rows": rows,
        "stale_or_missing_rows": stale_or_missing_rows,
        "stock_rows": stock_rows,
        "crypto_rows": crypto_rows,
        "crypto_diagnostics": crypto_diagnostics,
    }
