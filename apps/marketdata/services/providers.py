"""Data provider adapters.

V1: stubs only. We will add Polygon/Coinbase adapters once models + scanning
pipeline are proven end-to-end with fixture data.

Never store broker credentials. Only read-only market data keys.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    ts: object  # datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class ProviderError(RuntimeError):
    pass


class MarketDataProvider:
    """Interface for fetching price bars."""

    def fetch_bars(self, *, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        raise NotImplementedError
