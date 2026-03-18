from __future__ import annotations

from datetime import datetime, timezone

import requests

from .base import OHLCVBar


class YahooFinanceProvider:
    """Yahoo Finance chart endpoint for low-friction daily stock bars.

    This is intended for local/dev use so the project is not blocked on a paid
    market-data plan before minute data is actually needed.

    Supported in this milestone:
    - Stocks only
    - Daily bars only (1d)
    """

    def __init__(self, base_url: str = "https://query1.finance.yahoo.com"):
        self.base_url = base_url.rstrip("/")

    def fetch_bars(self, *, symbol: str, timeframe: str, limit: int):
        interval, range_value = _parse_timeframe_and_range(timeframe=timeframe, limit=limit)
        url = f"{self.base_url}/v8/finance/chart/{symbol}"
        params = {
            "interval": interval,
            "range": range_value,
            "includePrePost": "false",
            "events": "div,splits",
        }
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json() or {}

        chart = payload.get("chart") or {}
        error = chart.get("error")
        if error:
            description = error.get("description") or str(error)
            raise RuntimeError(f"Yahoo Finance error for {symbol}: {description}")

        result = (chart.get("result") or [None])[0]
        if not result:
            return

        timestamps = result.get("timestamp") or []
        indicators = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = indicators.get("open") or []
        highs = indicators.get("high") or []
        lows = indicators.get("low") or []
        closes = indicators.get("close") or []
        volumes = indicators.get("volume") or []

        rows = zip(timestamps, opens, highs, lows, closes, volumes)
        emitted = 0
        for ts_raw, o, h, l, c, v in rows:
            if None in {ts_raw, o, h, l, c}:
                continue
            ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            yield OHLCVBar(
                ts_utc=ts,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v or 0.0),
            )
            emitted += 1
            if limit and emitted >= int(limit):
                break


def _parse_timeframe_and_range(*, timeframe: str, limit: int) -> tuple[str, str]:
    tf = timeframe.strip().lower()
    if tf not in {"1d", "1day", "day"}:
        raise ValueError(
            "Yahoo Finance provider currently supports daily stock bars only (use timeframe=1d)."
        )

    # Conservative mapping: request a slightly larger window than needed so the
    # endpoint can absorb weekends/holidays while still yielding enough bars.
    if limit <= 100:
        range_value = "6mo"
    elif limit <= 300:
        range_value = "2y"
    elif limit <= 750:
        range_value = "5y"
    else:
        range_value = "10y"
    return "1d", range_value
