from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol


@dataclass(frozen=True)
class OHLCVBar:
    ts_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataProvider(Protocol):
    """Read-only market data provider.

    Implementations should return bars with timestamps in UTC.
    """

    def fetch_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> Iterable[OHLCVBar]:
        ...
