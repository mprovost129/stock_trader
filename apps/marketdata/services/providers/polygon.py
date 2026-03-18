from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from .base import OHLCVBar


class PolygonProvider:
    """Polygon.io aggregates provider.

    Current project intent:
    - Stocks: optional paid/entitled provider, mainly for minute bars later.
    - Crypto: not used by default because Coinbase public candles are simpler.
    """

    def __init__(self, api_key: str, base_url: str = "https://api.polygon.io"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def fetch_bars(self, *, symbol: str, timeframe: str, limit: int):
        mult, span = _parse_timeframe(timeframe)
        to_dt = datetime.now(tz=timezone.utc)
        from_dt = _compute_from_dt(to_dt=to_dt, span=span, mult=mult, limit=limit)
        url = (
            f"{self.base_url}/v2/aggs/ticker/{symbol}/range/{mult}/{span}/"
            f"{from_dt.date().isoformat()}/{to_dt.date().isoformat()}"
        )
        # Important: Polygon applies the limit before returning the payload. For wide
        # date ranges, requesting sort=asc can therefore return the oldest bars in the
        # window instead of the most recent bars we actually want. Request descending
        # data, then reverse it locally before persisting.
        params = {
            "adjusted": "true",
            "sort": "desc",
            "limit": int(limit),
            "apiKey": self.api_key,
        }
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json() or {}
        results = payload.get("results") or []
        if not results:
            return

        trimmed = results[: int(limit)] if limit else results
        ordered_rows = list(reversed(trimmed))
        for row in ordered_rows:
            ts = datetime.fromtimestamp(row["t"] / 1000.0, tz=timezone.utc)
            yield OHLCVBar(
                ts_utc=ts,
                open=float(row["o"]),
                high=float(row["h"]),
                low=float(row["l"]),
                close=float(row["c"]),
                volume=float(row.get("v") or 0.0),
            )


def _parse_timeframe(timeframe: str) -> tuple[int, str]:
    tf = timeframe.strip().lower()
    if tf in {"1m", "1min", "1minute"}:
        return 1, "minute"
    if tf in {"5m", "5min", "5minute"}:
        return 5, "minute"
    if tf in {"1d", "1day", "day"}:
        return 1, "day"
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _compute_from_dt(*, to_dt: datetime, span: str, mult: int, limit: int) -> datetime:
    bars = max(int(limit or 0), 1)
    if span == "day":
        # Add a generous holiday/weekend buffer.
        days = max(int(bars * mult * 1.8), 30)
        return to_dt - timedelta(days=days)
    if span == "minute":
        # Intraday requests need a much tighter but still buffered window.
        minutes = max(int(bars * mult * 2.5), 60)
        return to_dt - timedelta(minutes=minutes)
    raise ValueError(f"Unsupported span: {span}")
