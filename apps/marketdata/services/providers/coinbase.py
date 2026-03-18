from __future__ import annotations

from datetime import datetime, timezone

from .base import OHLCVBar, http_get_with_retry


class CoinbaseAdvancedProvider:
    """Coinbase Advanced Trade market data (public candles).

    We use the public candles endpoint which does NOT require auth.
    This keeps Milestone 1 simple and avoids credential handling.

    Coinbase returns candle arrays; timestamps are epoch seconds.
    """

    def __init__(self, base_url: str = "https://api.exchange.coinbase.com"):
        # Coinbase has multiple APIs; this endpoint is widely supported.
        self.base_url = base_url.rstrip("/")

    def fetch_bars(self, *, symbol: str, timeframe: str, limit: int):
        granularity = _granularity_seconds(timeframe)
        product_id = _to_product_id(symbol)
        url = f"{self.base_url}/products/{product_id}/candles"
        params = {
            "granularity": int(granularity),
        }
        r = http_get_with_retry(url, params=params, timeout=30)
        data = r.json() or []
        # Coinbase returns newest-first by default: [ time, low, high, open, close, volume ]
        data = list(reversed(data))
        if limit:
            data = data[-int(limit) :]
        for row in data:
            ts = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
            yield OHLCVBar(
                ts_utc=ts,
                open=float(row[3]),
                high=float(row[2]),
                low=float(row[1]),
                close=float(row[4]),
                volume=float(row[5]),
            )


def _granularity_seconds(timeframe: str) -> int:
    tf = timeframe.strip().lower()
    if tf in {"1m", "1min", "1minute"}:
        return 60
    if tf in {"5m", "5min", "5minute"}:
        return 300
    if tf in {"1d", "1day", "day"}:
        return 86400
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _to_product_id(symbol: str) -> str:
    # Opinionated: use USD quote unless the symbol already includes a dash.
    sym = symbol.upper().strip()
    if "-" in sym:
        return sym
    return f"{sym}-USD"
