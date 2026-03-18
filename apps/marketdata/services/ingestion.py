from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.marketdata.models import Instrument, PriceBar
from apps.marketdata.services.crypto_router import UnsupportedCryptoSymbolError, resolve_crypto_provider
from apps.marketdata.services.providers.binance import BinanceProvider
from apps.marketdata.services.providers.coinbase import CoinbaseAdvancedProvider
from apps.marketdata.services.providers.kraken import KrakenProvider
from apps.marketdata.services.providers.polygon import PolygonProvider
from apps.marketdata.services.providers.yahoo import YahooFinanceProvider


@dataclass(frozen=True)
class IngestResult:
    created: int
    updated: int
    skipped: int


def ingest_from_csv(*, symbol: str, timeframe: str, csv_path: str | Path) -> IngestResult:
    """Ingest OHLCV bars from a CSV file.

    CSV columns (header required):
      ts,open,high,low,close,volume

    - `ts` must be ISO-8601. If no timezone is provided, we assume UTC.
    - Upserts are safe due to uniq constraint on (instrument,timeframe,ts).
    """

    instrument = Instrument.objects.get(symbol=symbol.upper())
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    created = updated = skipped = 0

    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        required = {"ts", "open", "high", "low", "close", "volume"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"CSV must include header columns: {sorted(required)}")

        rows = list(reader)

    with transaction.atomic():
        for r in rows:
            ts = _parse_ts_utc(r["ts"])
            defaults = {
                "open": _d(r["open"]),
                "high": _d(r["high"]),
                "low": _d(r["low"]),
                "close": _d(r["close"]),
                "volume": _d(r["volume"]),
            }
            _, was_created = PriceBar.objects.update_or_create(
                instrument=instrument, timeframe=timeframe, ts=ts, defaults=defaults
            )
            if was_created:
                created += 1
            else:
                updated += 1

    return IngestResult(created=created, updated=updated, skipped=skipped)


def ingest_from_provider(*, symbol: str, timeframe: str, limit: int, provider_name: str | None = None) -> IngestResult:
    """Ingest OHLCV bars from an online provider.

    Provider policy for the current milestone:
    - STOCK daily bars default to Yahoo Finance (no paid plan required)
    - STOCK minute bars use Polygon only when explicitly configured/entitled
    - CRYPTO bars default to Coinbase public candles
    """

    instrument = Instrument.objects.get(symbol=symbol.upper())
    provider = _select_provider(
        instrument=instrument,
        timeframe=timeframe,
        provider_name=(provider_name or "").strip().lower() or None,
    )
    bars = provider.fetch_bars(symbol=instrument.symbol, timeframe=timeframe, limit=limit)
    return _save_bars(instrument=instrument, timeframe=timeframe, bars=bars)


def _select_provider(*, instrument: Instrument, timeframe: str, provider_name: str | None):
    if instrument.asset_class == Instrument.AssetClass.CRYPTO:
        if provider_name:
            if provider_name == "coinbase":
                return CoinbaseAdvancedProvider()
            if provider_name == "kraken":
                return KrakenProvider()
            if provider_name == "binance":
                return BinanceProvider()
            raise ValueError("Crypto ingestion supports provider=coinbase|kraken|binance.")
        resolved = resolve_crypto_provider(instrument.symbol)
        if resolved == "coinbase":
            return CoinbaseAdvancedProvider()
        if resolved == "kraken":
            return KrakenProvider()
        if resolved == "binance":
            return BinanceProvider()
        raise UnsupportedCryptoSymbolError(f"unsupported_crypto_pair:{instrument.symbol}")

    # Stocks
    if provider_name == "polygon":
        api_key = getattr(settings, "POLYGON_API_KEY", "")
        if not api_key:
            raise RuntimeError("provider=polygon requires POLYGON_API_KEY to be configured")
        return PolygonProvider(api_key=api_key)

    if provider_name == "yahoo":
        return YahooFinanceProvider()

    default_provider = getattr(settings, "STOCK_DAILY_PROVIDER", "yahoo").strip().lower() or "yahoo"
    if timeframe.strip().lower() in {"1d", "1day", "day"}:
        if default_provider == "polygon":
            api_key = getattr(settings, "POLYGON_API_KEY", "")
            if api_key:
                return PolygonProvider(api_key=api_key)
        return YahooFinanceProvider()

    api_key = getattr(settings, "POLYGON_API_KEY", "")
    if api_key:
        return PolygonProvider(api_key=api_key)

    raise RuntimeError(
        "Intraday stock bars currently require provider=polygon with POLYGON_API_KEY. "
        "For daily stock bars, use timeframe=1d (Yahoo Finance is the default)."
    )


def _save_bars(*, instrument: Instrument, timeframe: str, bars: Iterable) -> IngestResult:
    created = updated = skipped = 0
    with transaction.atomic():
        for b in bars:
            defaults = {
                "open": _d(b.open),
                "high": _d(b.high),
                "low": _d(b.low),
                "close": _d(b.close),
                "volume": _d(b.volume),
            }
            _, was_created = PriceBar.objects.update_or_create(
                instrument=instrument, timeframe=timeframe, ts=b.ts_utc, defaults=defaults
            )
            if was_created:
                created += 1
            else:
                updated += 1
    return IngestResult(created=created, updated=updated, skipped=skipped)


def _parse_ts_utc(raw: str) -> datetime:
    dt = parse_datetime(raw)
    if dt is None:
        raise ValueError(f"Invalid datetime: {raw}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _d(v) -> Decimal:
    return Decimal(str(v))
