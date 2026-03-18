from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol

import requests


def http_get_with_retry(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: int = 30, max_retries: int = 3, backoff_base: float = 2.0) -> requests.Response:
    """GET with exponential backoff on transient errors (429, 5xx).

    4xx errors other than 429 are raised immediately (bad request, bad symbol,
    auth failure — retrying won't help).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < max_retries - 1:
                    time.sleep(backoff_base ** attempt)
                    last_exc = requests.HTTPError(response=r)
                    continue
                r.raise_for_status()
            r.raise_for_status()
            return r
        except requests.HTTPError:
            raise
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(backoff_base ** attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("http_get_with_retry: unexpected exit")


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
