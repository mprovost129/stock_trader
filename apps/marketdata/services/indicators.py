"""Indicator helpers.

V1 goal: keep indicators pure and testable.
Avoid pandas dependency initially; simple lists in/out.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class OHLCV:
    ts: object  # datetime in practice
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def sma(values: list[Decimal], length: int) -> list[Decimal | None]:
    if length <= 0:
        raise ValueError("length must be > 0")

    out: list[Decimal | None] = []
    window_sum: Decimal = Decimal("0")
    for i, v in enumerate(values):
        window_sum += v
        if i >= length:
            window_sum -= values[i - length]
        if i + 1 >= length:
            out.append(window_sum / Decimal(length))
        else:
            out.append(None)
    return out


def true_range(ohlcv: list[OHLCV]) -> list[Decimal | None]:
    """True range series (same length as input).

    TR[i] = max(high-low, abs(high-prev_close), abs(low-prev_close))
    TR[0] = high-low
    """
    out: list[Decimal | None] = []
    prev_close: Decimal | None = None
    for i, b in enumerate(ohlcv):
        if i == 0 or prev_close is None:
            tr = b.high - b.low
        else:
            tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        out.append(tr)
        prev_close = b.close
    return out


def atr(ohlcv: list[OHLCV], length: int = 14) -> list[Decimal | None]:
    """Wilder's ATR.

    Returns ATR series (same length), None until enough bars.
    """
    if length <= 0:
        raise ValueError("length must be > 0")
    tr = true_range(ohlcv)
    out: list[Decimal | None] = [None] * len(tr)
    if len(tr) < length:
        return out

    # first ATR is SMA of first `length` TRs
    first = sum([t for t in tr[:length] if t is not None], Decimal("0")) / Decimal(length)
    out[length - 1] = first
    prev_atr = first
    for i in range(length, len(tr)):
        t = tr[i]
        if t is None:
            out[i] = None
            continue
        # Wilder smoothing
        cur = (prev_atr * (Decimal(length - 1)) + t) / Decimal(length)
        out[i] = cur
        prev_atr = cur
    return out


def rsi(closes: list[Decimal], period: int) -> list[Decimal | None]:
    """Wilder's RSI.

    Returns RSI series (same length), None until enough bars (first valid at index `period`).
    """
    if period <= 0:
        raise ValueError("period must be > 0")
    out: list[Decimal | None] = [None] * len(closes)
    if len(closes) <= period:
        return out

    # Seed: simple average of first `period` gains/losses (changes at indices 1..period)
    avg_gain = Decimal("0")
    avg_loss = Decimal("0")
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change > 0:
            avg_gain += change
        else:
            avg_loss += abs(change)
    avg_gain /= Decimal(period)
    avg_loss /= Decimal(period)

    def _calc(ag: Decimal, al: Decimal) -> Decimal:
        if al == 0:
            return Decimal("100")
        return Decimal("100") - (Decimal("100") / (Decimal("1") + ag / al))

    out[period] = _calc(avg_gain, avg_loss)

    # Wilder smoothing for remaining bars
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = change if change > 0 else Decimal("0")
        loss = abs(change) if change < 0 else Decimal("0")
        avg_gain = (avg_gain * Decimal(period - 1) + gain) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + loss) / Decimal(period)
        out[i] = _calc(avg_gain, avg_loss)

    return out


def rolling_high(values: list[Decimal], period: int) -> list[Decimal | None]:
    """Rolling maximum over a window of `period` bars (inclusive of current bar).

    Returns None until a full window is available (index period-1).
    """
    if period <= 0:
        raise ValueError("period must be > 0")
    out: list[Decimal | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(max(values[i - period + 1 : i + 1]))
    return out


def rolling_low(values: list[Decimal], period: int) -> list[Decimal | None]:
    """Rolling minimum over a window of `period` bars (inclusive of current bar).

    Returns None until a full window is available (index period-1).
    """
    if period <= 0:
        raise ValueError("period must be > 0")
    out: list[Decimal | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(min(values[i - period + 1 : i + 1]))
    return out


def current_market_regime(
    *,
    closes: list[Decimal],
    highs: list[Decimal],
    lows: list[Decimal],
    volumes: list[Decimal],
    trend_length: int = 20,
) -> str:
    """Classify the current market regime.

    Returns one of: 'TRENDING', 'SIDEWAYS', 'VOLATILE', or 'UNKNOWN' (insufficient data).

    Logic:
    - ATR as a % of price > 4% → VOLATILE
    - Absolute price drift over trend_length bars > 3% → TRENDING
    - Otherwise → SIDEWAYS
    """
    n = len(closes)
    if n < trend_length + 1:
        return "UNKNOWN"

    ohlcv_bars = [
        OHLCV(
            ts=i,
            open=closes[i - 1] if i > 0 else closes[0],
            high=highs[i],
            low=lows[i],
            close=closes[i],
            volume=volumes[i],
        )
        for i in range(n)
    ]
    atr_series = atr(ohlcv_bars, trend_length)
    cur_idx = n - 1
    cur_atr = atr_series[cur_idx]
    if cur_atr is None:
        return "UNKNOWN"

    cur_close = closes[cur_idx]
    past_close = closes[cur_idx - trend_length]
    if cur_close == 0:
        return "UNKNOWN"

    atr_pct = cur_atr / cur_close
    price_change_pct = abs(cur_close - past_close) / max(abs(past_close), Decimal("0.00000001"))

    if atr_pct > Decimal("0.04"):
        return "VOLATILE"
    if price_change_pct > Decimal("0.03"):
        return "TRENDING"
    return "SIDEWAYS"
