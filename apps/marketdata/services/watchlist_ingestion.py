from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from django.db.models import Count, Max

from apps.marketdata.models import Instrument, PriceBar
from apps.marketdata.services.ingestion import IngestResult, ingest_from_provider
from apps.marketdata.services.crypto_router import UnsupportedCryptoSymbolError, resolve_crypto_provider, valid_crypto_symbol
from apps.marketdata.services.ingestion_state import active_provider_cooldown_reason, get_unsupported_crypto_reason, mark_provider_cooldown, mark_unsupported_crypto_symbol
from apps.portfolios.models import InstrumentSelection, Watchlist


@dataclass(frozen=True)
class WatchlistIngestItem:
    instrument: Instrument
    timeframe: str
    provider_name: str | None
    has_bars: bool
    bar_count: int
    latest_ts: datetime | None


@dataclass(frozen=True)
class WatchlistIngestSummary:
    watchlist_name: str
    selected_count: int
    planned_count: int
    success_count: int
    failure_count: int
    created_total: int
    updated_total: int
    skipped_total: int
    missing_count: int
    refreshed_count: int


def build_watchlist_ingest_plan(
    *,
    watchlist: Watchlist,
    stock_timeframe: str = "1d",
    crypto_timeframe: str = "1d",
    stock_provider: str | None = None,
    crypto_provider: str | None = None,
    symbols: Iterable[str] | None = None,
    asset_class: str | None = None,
    max_symbols: int | None = None,
) -> tuple[list[WatchlistIngestItem], int]:
    symbol_filter = {str(s).strip().upper() for s in (symbols or []) if str(s).strip()}
    selections_qs = (
        InstrumentSelection.objects.select_related("instrument")
        .filter(watchlist=watchlist, is_active=True, instrument__is_active=True)
        .order_by("instrument__asset_class", "instrument__symbol")
    )
    if asset_class:
        selections_qs = selections_qs.filter(instrument__asset_class=asset_class)

    selections = list(selections_qs)
    if symbol_filter:
        selections = [sel for sel in selections if sel.instrument.symbol.upper() in symbol_filter]

    if not selections:
        return [], 0

    instrument_ids = [sel.instrument_id for sel in selections]
    bar_stats = {}
    for timeframe in {stock_timeframe, crypto_timeframe}:
        stats_qs = (
            PriceBar.objects.filter(instrument_id__in=instrument_ids, timeframe=timeframe)
            .values("instrument_id")
            .annotate(bar_count=Count("id"), latest_ts=Max("ts"))
        )
        for row in stats_qs:
            bar_stats[(row["instrument_id"], timeframe)] = {
                "bar_count": int(row.get("bar_count") or 0),
                "latest_ts": row.get("latest_ts"),
            }

    planned: list[WatchlistIngestItem] = []
    for selection in selections:
        instrument = selection.instrument
        timeframe = stock_timeframe if instrument.asset_class == Instrument.AssetClass.STOCK else crypto_timeframe
        if instrument.asset_class == Instrument.AssetClass.STOCK:
            provider_name = stock_provider
        else:
            if get_unsupported_crypto_reason(instrument.symbol):
                continue
            provider_name = crypto_provider or None
            if not provider_name:
                if valid_crypto_symbol(instrument.symbol):
                    try:
                        provider_name = resolve_crypto_provider(instrument.symbol)
                    except UnsupportedCryptoSymbolError as exc:
                        mark_unsupported_crypto_symbol(instrument.symbol, str(exc))
                        provider_name = None
                    except Exception:
                        provider_name = None
                else:
                    provider_name = None
        if active_provider_cooldown_reason(instrument.symbol, provider_name):
            continue
        stats = bar_stats.get((instrument.id, timeframe), {})
        bar_count = int(stats.get("bar_count") or 0)
        latest_ts = stats.get("latest_ts")
        if instrument.asset_class == Instrument.AssetClass.CRYPTO and not provider_name:
            continue

        planned.append(
            WatchlistIngestItem(
                instrument=instrument,
                timeframe=timeframe,
                provider_name=provider_name,
                has_bars=bar_count > 0,
                bar_count=bar_count,
                latest_ts=latest_ts,
            )
        )

    # Missing bars first, then stalest bars first, then symbol.
    def sort_key(item: WatchlistIngestItem):
        latest_key = item.latest_ts or datetime.min.replace(tzinfo=None)
        if getattr(latest_key, "tzinfo", None) is not None:
            latest_key = latest_key.replace(tzinfo=None)
        return (1 if item.has_bars else 0, latest_key, item.instrument.symbol)

    planned.sort(key=sort_key)
    selected_count = len(planned)
    if max_symbols and max_symbols > 0:
        planned = planned[:max_symbols]
    return planned, selected_count


def execute_watchlist_ingest_plan(
    *,
    watchlist: Watchlist,
    items: list[WatchlistIngestItem],
    throttle_seconds: float = 0.0,
    limit: int = 300,
    on_success=None,
    on_failure=None,
) -> WatchlistIngestSummary:
    success_count = failure_count = created_total = updated_total = skipped_total = 0
    missing_count = refreshed_count = 0

    for idx, item in enumerate(items, start=1):
        try:
            result: IngestResult = ingest_from_provider(
                symbol=item.instrument.symbol,
                timeframe=item.timeframe,
                limit=limit,
                provider_name=item.provider_name,
            )
            created_total += result.created
            updated_total += result.updated
            skipped_total += result.skipped
            success_count += 1
            if item.has_bars:
                refreshed_count += 1
            else:
                missing_count += 1
            if on_success:
                on_success(idx, item, result)
        except Exception as exc:  # noqa: BLE001
            failure_count += 1
            msg = str(exc)
            if '429' in msg:
                mark_provider_cooldown(item.instrument.symbol, item.provider_name, ttl_seconds=1800, reason='rate_limited')
            if item.instrument.asset_class == Instrument.AssetClass.CRYPTO and ('404' in msg or 'unsupported_crypto_pair' in msg or 'invalid_or_unsupported_crypto_symbol' in msg):
                mark_unsupported_crypto_symbol(item.instrument.symbol, msg)
            if on_failure:
                on_failure(idx, item, exc)
        if throttle_seconds and idx < len(items):
            time.sleep(throttle_seconds)

    return WatchlistIngestSummary(
        watchlist_name=watchlist.name,
        selected_count=len(items),
        planned_count=len(items),
        success_count=success_count,
        failure_count=failure_count,
        created_total=created_total,
        updated_total=updated_total,
        skipped_total=skipped_total,
        missing_count=missing_count,
        refreshed_count=refreshed_count,
    )
