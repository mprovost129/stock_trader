"""Moving average crossover with transparent opportunity scoring.

This remains a read-only decision-support strategy.

Modes
- event: emit only on a fresh crossover bar
- state: emit current bullish/bearish bias with an opportunity score

Score model (0-100)
- trend alignment / separation: 35
- fast MA slope / momentum: 25
- volume confirmation: 20
- volatility quality: 10
- event bonus or persistence quality: 10
"""

from __future__ import annotations

from decimal import Decimal

from apps.marketdata.services.indicators import OHLCV, atr, sma

from ..registry import StrategySignal, register, register_diagnostics

HUNDRED = Decimal("100")


def _clamp01(value: Decimal) -> Decimal:
    if value < 0:
        return Decimal("0")
    if value > 1:
        return Decimal("1")
    return value


def _to_pct(value: Decimal, *, places: str = "0.01") -> float:
    return float((value * HUNDRED).quantize(Decimal(places)))


def _build_ohlcv(*, highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], volumes: list[Decimal]) -> list[OHLCV]:
    out: list[OHLCV] = []
    prev_close = closes[0] if closes else Decimal("0")
    for i, close in enumerate(closes):
        open_value = prev_close if i > 0 else close
        out.append(OHLCV(ts=i, open=open_value, high=highs[i], low=lows[i], close=close, volume=volumes[i]))
        prev_close = close
    return out


def _score_components(
    *,
    closes: list[Decimal],
    highs: list[Decimal],
    lows: list[Decimal],
    volumes: list[Decimal],
    fast_len: int,
    slow_len: int,
    event_fired: bool,
) -> tuple[dict[str, float], dict[str, Decimal]]:
    fast = sma(closes, fast_len)
    slow = sma(closes, slow_len)
    i = len(closes) - 1
    prev = i - 1
    prev3 = max(0, i - 3)

    cur_fast = fast[i]
    prev_fast = fast[prev]
    older_fast = fast[prev3] if fast[prev3] is not None else prev_fast
    cur_slow = slow[i]
    prev_slow = slow[prev]

    close = closes[i]
    prev_close = closes[prev]
    cur_spread = cur_fast - cur_slow
    prev_spread = prev_fast - prev_slow
    spread_abs_pct = abs(cur_spread) / max(abs(cur_slow), Decimal("0.00000001"))
    trend_strength_norm = _clamp01(spread_abs_pct / Decimal("0.03"))

    fast_slope_pct = (cur_fast - older_fast) / max(abs(older_fast), Decimal("0.00000001"))
    slope_norm = _clamp01(abs(fast_slope_pct) / Decimal("0.02"))

    vol_window = volumes[-20:] if len(volumes) >= 20 else volumes
    avg_volume = sum(vol_window, Decimal("0")) / Decimal(len(vol_window) or 1)
    volume_ratio = volumes[i] / max(avg_volume, Decimal("0.00000001"))
    volume_norm = _clamp01(volume_ratio / Decimal("1.5"))

    ohlcv = _build_ohlcv(highs=highs, lows=lows, closes=closes, volumes=volumes)
    atr_series = atr(ohlcv, 14)
    current_atr = atr_series[i]
    atr_pct = (current_atr / close) if current_atr is not None and close else Decimal("0")
    volatility_norm = _clamp01(Decimal("1") - abs(atr_pct - Decimal("0.03")) / Decimal("0.03"))

    price_trend_agreement = Decimal("1") if ((close >= cur_fast and cur_fast >= cur_slow) or (close <= cur_fast and cur_fast <= cur_slow)) else Decimal("0.35")
    event_norm = Decimal("1") if event_fired else _clamp01((abs(cur_spread) / max(abs(prev_spread), Decimal("0.00000001"))))

    components_dec = {
        "trend": _clamp01(trend_strength_norm * Decimal("35")),
        "momentum": _clamp01(slope_norm) * Decimal("25"),
        "volume": _clamp01(volume_norm) * Decimal("20"),
        "volatility": _clamp01(volatility_norm) * Decimal("10"),
        "quality": _clamp01((price_trend_agreement + event_norm) / Decimal("2")) * Decimal("10"),
    }
    total = sum(components_dec.values(), Decimal("0"))
    components_float = {k: float(v.quantize(Decimal("0.01"))) for k, v in components_dec.items()}
    raw = {
        "cur_spread": cur_spread,
        "prev_spread": prev_spread,
        "fast_slope_pct": fast_slope_pct,
        "volume_ratio": volume_ratio,
        "atr_pct": atr_pct,
        "total": total,
        "close": close,
        "prev_close": prev_close,
    }
    return components_float, raw


def _emit_state_signal(*, direction: str, fast_len: int, slow_len: int, raw: dict[str, Decimal], components: dict[str, float]) -> StrategySignal:
    spread = raw["cur_spread"]
    total = raw["total"]
    label = "BULLISH_STATE" if direction == "LONG" else "BEARISH_STATE"
    side = "Bullish" if direction == "LONG" else "Bearish"
    return StrategySignal(
        direction=direction,
        score=float(total.quantize(Decimal("0.01"))),
        rationale=(
            f"{side} state scored {float(total.quantize(Decimal('0.01'))):.2f}/100. "
            f"Fast MA({fast_len}) remains {'above' if direction == 'LONG' else 'below'} Slow MA({slow_len}); "
            f"latest spread={spread:.4f}."
        ),
        signal_kind="STATE",
        signal_label=label,
        score_components=components,
    )


def _emit_event_signal(*, direction: str, fast_len: int, slow_len: int, raw: dict[str, Decimal], components: dict[str, float]) -> StrategySignal:
    total = raw["total"]
    label = "BULLISH_CROSS" if direction == "LONG" else "BEARISH_CROSS"
    side = "Bullish" if direction == "LONG" else "Bearish"
    return StrategySignal(
        direction=direction,
        score=float(total.quantize(Decimal("0.01"))),
        rationale=(
            f"{side} crossover scored {float(total.quantize(Decimal('0.01'))):.2f}/100. "
            f"Fast MA({fast_len}) crossed {'above' if direction == 'LONG' else 'below'} Slow MA({slow_len})."
        ),
        signal_kind="EVENT",
        signal_label=label,
        score_components=components,
    )


@register("moving_average_crossover")
@register("ma_crossover")
def ma_crossover(
    *,
    closes: list[Decimal],
    highs: list[Decimal] | None = None,
    lows: list[Decimal] | None = None,
    volumes: list[Decimal] | None = None,
    fast_len: int = 20,
    slow_len: int = 50,
    signal_mode: str = "event",
    **kwargs,
) -> StrategySignal | None:
    if "fast_ma" in kwargs:
        fast_len = int(kwargs.pop("fast_ma"))
    if "slow_ma" in kwargs:
        slow_len = int(kwargs.pop("slow_ma"))
    if "signal_mode" in kwargs:
        signal_mode = str(kwargs.pop("signal_mode") or "event").lower()
    kwargs.clear()

    highs = highs or closes
    lows = lows or closes
    volumes = volumes or [Decimal("0")] * len(closes)

    if slow_len <= fast_len:
        raise ValueError("slow_len must be > fast_len")
    if len(closes) < max(slow_len + 2, 20):
        return None

    fast = sma(closes, fast_len)
    slow = sma(closes, slow_len)
    i = len(closes) - 1
    prev = i - 1

    if fast[prev] is None or slow[prev] is None or fast[i] is None or slow[i] is None:
        return None

    prev_fast = fast[prev]
    prev_slow = slow[prev]
    cur_fast = fast[i]
    cur_slow = slow[i]

    bullish_cross = prev_fast <= prev_slow and cur_fast > cur_slow
    bearish_cross = prev_fast >= prev_slow and cur_fast < cur_slow

    if signal_mode == "state":
        if cur_fast == cur_slow:
            return StrategySignal(
                direction="FLAT",
                score=50.0,
                rationale=f"Neutral state scored 50.00/100. Fast MA({fast_len}) equals Slow MA({slow_len}).",
                signal_kind="STATE",
                signal_label="NEUTRAL_STATE",
                score_components={"trend": 0.0, "momentum": 0.0, "volume": 0.0, "volatility": 0.0, "quality": 50.0},
            )
        direction = "LONG" if cur_fast > cur_slow else "SHORT"
        components, raw = _score_components(
            closes=closes, highs=highs, lows=lows, volumes=volumes, fast_len=fast_len, slow_len=slow_len, event_fired=False
        )
        return _emit_state_signal(direction=direction, fast_len=fast_len, slow_len=slow_len, raw=raw, components=components)

    if bullish_cross:
        components, raw = _score_components(
            closes=closes, highs=highs, lows=lows, volumes=volumes, fast_len=fast_len, slow_len=slow_len, event_fired=True
        )
        return _emit_event_signal(direction="LONG", fast_len=fast_len, slow_len=slow_len, raw=raw, components=components)

    if bearish_cross:
        components, raw = _score_components(
            closes=closes, highs=highs, lows=lows, volumes=volumes, fast_len=fast_len, slow_len=slow_len, event_fired=True
        )
        return _emit_event_signal(direction="SHORT", fast_len=fast_len, slow_len=slow_len, raw=raw, components=components)

    return None


@register_diagnostics("moving_average_crossover")
@register_diagnostics("ma_crossover")
def ma_crossover_diagnostics(
    *,
    closes: list[Decimal],
    highs: list[Decimal] | None = None,
    lows: list[Decimal] | None = None,
    volumes: list[Decimal] | None = None,
    fast_len: int = 20,
    slow_len: int = 50,
    signal_mode: str = "event",
    **kwargs,
) -> str:
    if "fast_ma" in kwargs:
        fast_len = int(kwargs.pop("fast_ma"))
    if "slow_ma" in kwargs:
        slow_len = int(kwargs.pop("slow_ma"))
    if "signal_mode" in kwargs:
        signal_mode = str(kwargs.pop("signal_mode") or "event").lower()
    kwargs.clear()

    highs = highs or closes
    lows = lows or closes
    volumes = volumes or [Decimal("0")] * len(closes)

    if slow_len <= fast_len:
        return f"invalid config: slow_len({slow_len}) must be greater than fast_len({fast_len})"
    if len(closes) < max(slow_len + 2, 20):
        return f"insufficient bars: need at least {max(slow_len + 2, 20)}, have {len(closes)}"

    fast = sma(closes, fast_len)
    slow = sma(closes, slow_len)
    i = len(closes) - 1
    prev = i - 1

    if fast[prev] is None or slow[prev] is None or fast[i] is None or slow[i] is None:
        return "moving-average values unavailable on the latest bars"

    prev_fast = fast[prev]
    prev_slow = slow[prev]
    cur_fast = fast[i]
    cur_slow = slow[i]

    bullish_cross = prev_fast <= prev_slow and cur_fast > cur_slow
    bearish_cross = prev_fast >= prev_slow and cur_fast < cur_slow
    delta_prev = prev_fast - prev_slow
    delta_cur = cur_fast - cur_slow
    relationship = "above" if delta_cur > 0 else "below" if delta_cur < 0 else "equal to"

    event_fired = bullish_cross or bearish_cross
    components, raw = _score_components(
        closes=closes, highs=highs, lows=lows, volumes=volumes, fast_len=fast_len, slow_len=slow_len, event_fired=event_fired
    )
    total = raw["total"]

    if signal_mode == "state":
        if delta_cur > 0:
            return (
                f"bullish state score={float(total.quantize(Decimal('0.01'))):.2f}/100: fast MA({fast_len}) is above slow MA({slow_len}); "
                f"prev_spread={delta_prev:.4f}, cur_spread={delta_cur:.4f}, components={components}"
            )
        if delta_cur < 0:
            return (
                f"bearish state score={float(total.quantize(Decimal('0.01'))):.2f}/100: fast MA({fast_len}) is below slow MA({slow_len}); "
                f"prev_spread={delta_prev:.4f}, cur_spread={delta_cur:.4f}, components={components}"
            )
        return f"neutral state score=50.00/100: fast MA({fast_len}) equals slow MA({slow_len})"

    if bullish_cross:
        return f"signal fired: bullish crossover score={float(total.quantize(Decimal('0.01'))):.2f}/100, components={components}"
    if bearish_cross:
        return f"signal fired: bearish crossover score={float(total.quantize(Decimal('0.01'))):.2f}/100, components={components}"

    return (
        f"no crossover on latest bar: fast MA({fast_len}) is {relationship} slow MA({slow_len}); "
        f"prev_spread={delta_prev:.4f}, cur_spread={delta_cur:.4f}, model_score={float(total.quantize(Decimal('0.01'))):.2f}/100"
    )
