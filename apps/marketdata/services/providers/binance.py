from __future__ import annotations

from datetime import datetime, timezone

from .base import OHLCVBar, http_get_with_retry


class BinanceProvider:
    def __init__(self, base_url: str = "https://api.binance.com"):
        self.base_url = base_url.rstrip("/")

    def fetch_bars(self, *, symbol: str, timeframe: str, limit: int):
        pair = _to_pair(symbol)
        interval = _interval(timeframe)
        url = f"{self.base_url}/api/v3/klines"
        params = {"symbol": pair, "interval": interval, "limit": int(limit or 300)}
        r = http_get_with_retry(url, params=params, timeout=30)
        data = r.json() or []
        for row in data:
            ts = datetime.fromtimestamp(int(row[0]) / 1000.0, tz=timezone.utc)
            yield OHLCVBar(ts_utc=ts, open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]), volume=float(row[5]))


def _interval(timeframe: str) -> str:
    tf = timeframe.strip().lower()
    if tf in {"1m", "1min", "1minute"}:
        return "1m"
    if tf in {"5m", "5min", "5minute"}:
        return "5m"
    if tf in {"1d", "1day", "day"}:
        return "1d"
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _to_pair(symbol: str) -> str:
    return f"{symbol.upper().strip()}USDT"
