from __future__ import annotations

from datetime import datetime, timezone

from .base import OHLCVBar, http_get_with_retry


class KrakenProvider:
    def __init__(self, base_url: str = "https://api.kraken.com"):
        self.base_url = base_url.rstrip("/")

    def fetch_bars(self, *, symbol: str, timeframe: str, limit: int):
        pair = _to_pair(symbol)
        interval = _interval(timeframe)
        url = f"{self.base_url}/0/public/OHLC"
        params = {"pair": pair, "interval": interval}
        r = http_get_with_retry(url, params=params, timeout=30)
        payload = r.json() or {}
        errors = payload.get("error") or []
        if errors:
            raise RuntimeError(f"Kraken error for {pair}: {', '.join(errors)}")
        result = payload.get("result") or {}
        rows = result.get(pair) or []
        if not rows:
            return
        data = rows[-int(limit) :] if limit else rows
        for row in data:
            ts = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
            yield OHLCVBar(ts_utc=ts, open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]), volume=float(row[6]))


def _interval(timeframe: str) -> int:
    tf = timeframe.strip().lower()
    if tf in {"1m", "1min", "1minute"}:
        return 1
    if tf in {"5m", "5min", "5minute"}:
        return 5
    if tf in {"1d", "1day", "day"}:
        return 1440
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _to_pair(symbol: str) -> str:
    sym = symbol.upper().strip()
    if sym == "BTC":
        return "XBTUSD"
    return f"{sym}USD"
