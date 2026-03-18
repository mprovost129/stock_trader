"""Composite multi-factor trading brain.

Read-only decision support. Emits EVENT or STATE signals with a composite
0-100 opportunity score built from:
- moving-average trend alignment
- RSI reversal / momentum context
- breakout / breakdown confirmation
- volume spike confirmation
- volatility quality

This is intentionally transparent rather than "magical".
"""

from __future__ import annotations

from decimal import Decimal

from apps.marketdata.services.indicators import OHLCV, atr, current_market_regime, rolling_high, rolling_low, rsi, sma

from ..registry import StrategySignal, register, register_diagnostics


ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


def _clamp01(value: Decimal) -> Decimal:
    if value < ZERO:
        return ZERO
    if value > ONE:
        return ONE
    return value


def _build_ohlcv(*, highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], volumes: list[Decimal]) -> list[OHLCV]:
    out: list[OHLCV] = []
    prev_close = closes[0] if closes else ZERO
    for i, close in enumerate(closes):
        open_value = prev_close if i > 0 else close
        out.append(OHLCV(ts=i, open=open_value, high=highs[i], low=lows[i], close=close, volume=volumes[i]))
        prev_close = close
    return out


def _quant(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _score(
    *, closes: list[Decimal], highs: list[Decimal], lows: list[Decimal], volumes: list[Decimal],
    fast_ma: int, slow_ma: int, rsi_len: int, breakout_len: int, volume_window: int, event_bonus: bool,
    regime_window: int,
) -> tuple[Decimal, dict[str, float], dict[str, object]]:
    fast = sma(closes, fast_ma)
    slow = sma(closes, slow_ma)
    rsi_series = rsi(closes, rsi_len)
    highs_roll = rolling_high(closes, breakout_len)
    lows_roll = rolling_low(closes, breakout_len)
    ohlcv = _build_ohlcv(highs=highs, lows=lows, closes=closes, volumes=volumes)
    atr_series = atr(ohlcv, 14)

    i = len(closes) - 1
    prev = i - 1
    prev_break_high = highs_roll[prev] if highs_roll[prev] is not None else closes[i]
    prev_break_low = lows_roll[prev] if lows_roll[prev] is not None else closes[i]

    cur_fast = fast[i]
    cur_slow = slow[i]
    prev_fast = fast[prev]
    prev_slow = slow[prev]
    cur_close = closes[i]
    cur_rsi = rsi_series[i] if rsi_series[i] is not None else Decimal('50')
    current_atr = atr_series[i] if atr_series[i] is not None else ZERO

    trend_norm = _clamp01(abs(cur_fast - cur_slow) / max(abs(cur_slow), Decimal('0.00000001')) / Decimal('0.03'))
    momentum_norm = _clamp01(abs(cur_fast - prev_fast) / max(abs(prev_fast), Decimal('0.00000001')) / Decimal('0.02'))

    vol_window = volumes[-volume_window:] if len(volumes) >= volume_window else volumes
    avg_volume = sum(vol_window, ZERO) / Decimal(len(vol_window) or 1)
    volume_ratio = volumes[i] / max(avg_volume, Decimal('0.00000001'))
    volume_norm = _clamp01(volume_ratio / Decimal('2'))

    atr_pct = current_atr / max(abs(cur_close), Decimal('0.00000001')) if cur_close else ZERO
    volatility_norm = _clamp01(ONE - abs(atr_pct - Decimal('0.03')) / Decimal('0.03'))

    direction = 'LONG' if cur_fast > cur_slow else 'SHORT' if cur_fast < cur_slow else 'FLAT'
    breakout_long = cur_close > prev_break_high
    breakout_short = cur_close < prev_break_low

    if direction == 'LONG':
        rsi_bias = _clamp01((Decimal('55') - cur_rsi) / Decimal('25')) if cur_rsi <= Decimal('55') else _clamp01((cur_rsi - Decimal('55')) / Decimal('45')) * Decimal('0.4')
        breakout_norm = ONE if breakout_long else ZERO
    elif direction == 'SHORT':
        rsi_bias = _clamp01((cur_rsi - Decimal('45')) / Decimal('25')) if cur_rsi >= Decimal('45') else _clamp01((Decimal('45') - cur_rsi) / Decimal('45')) * Decimal('0.4')
        breakout_norm = ONE if breakout_short else ZERO
    else:
        rsi_bias = ZERO
        breakout_norm = ZERO

    regime = current_market_regime(closes=closes, highs=highs, lows=lows, volumes=volumes, trend_length=regime_window)
    if regime == 'TRENDING':
        weights = {
            'trend': Decimal('35'),
            'momentum': Decimal('20'),
            'rsi': Decimal('10'),
            'breakout': Decimal('20'),
            'volume': Decimal('10'),
            'volatility': Decimal('5'),
        }
    elif regime == 'SIDEWAYS':
        weights = {
            'trend': Decimal('15'),
            'momentum': Decimal('15'),
            'rsi': Decimal('30'),
            'breakout': Decimal('10'),
            'volume': Decimal('10'),
            'volatility': Decimal('20'),
        }
    elif regime == 'VOLATILE':
        weights = {
            'trend': Decimal('20'),
            'momentum': Decimal('10'),
            'rsi': Decimal('10'),
            'breakout': Decimal('15'),
            'volume': Decimal('20'),
            'volatility': Decimal('25'),
        }
    else:  # QUIET / UNKNOWN
        weights = {
            'trend': Decimal('25'),
            'momentum': Decimal('20'),
            'rsi': Decimal('20'),
            'breakout': Decimal('10'),
            'volume': Decimal('10'),
            'volatility': Decimal('15'),
        }

    parts_dec = {
        'trend': trend_norm * weights['trend'],
        'momentum': momentum_norm * weights['momentum'],
        'rsi': _clamp01(rsi_bias) * weights['rsi'],
        'breakout': _clamp01(breakout_norm) * weights['breakout'],
        'volume': volume_norm * weights['volume'],
        'volatility': volatility_norm * weights['volatility'],
    }
    total = sum(parts_dec.values(), ZERO)
    # persistence bonus for aligned state and event bonus for fresh trigger
    total += (Decimal('5') if event_bonus else ZERO)
    total = min(HUNDRED, total)
    parts = {k: _quant(v) for k, v in parts_dec.items()}
    raw = {
        'direction': direction,
        'cur_fast': cur_fast,
        'cur_slow': cur_slow,
        'prev_fast': prev_fast,
        'prev_slow': prev_slow,
        'cur_rsi': cur_rsi,
        'volume_ratio': volume_ratio,
        'atr_pct': atr_pct,
        'breakout_long': breakout_long,
        'breakout_short': breakout_short,
        'total': total,
        'cur_close': cur_close,
        'regime': regime,
    }
    return total, parts, raw


def _event_label(direction: str) -> str:
    return 'BULLISH_BRAIN' if direction == 'LONG' else 'BEARISH_BRAIN' if direction == 'SHORT' else 'NEUTRAL_BRAIN'


def _state_label(direction: str) -> str:
    return 'BULLISH_STACK' if direction == 'LONG' else 'BEARISH_STACK' if direction == 'SHORT' else 'NEUTRAL_STACK'


@register('trading_brain')
def trading_brain(
    *, closes: list[Decimal], highs: list[Decimal] | None = None, lows: list[Decimal] | None = None,
    volumes: list[Decimal] | None = None, fast_ma: int = 5, slow_ma: int = 10, rsi_len: int = 14,
    breakout_len: int = 20, volume_window: int = 20, regime_window: int = 20, signal_mode: str = 'state', min_score: float = 50.0, **kwargs,
) -> StrategySignal | None:
    highs = highs or closes
    lows = lows or closes
    volumes = volumes or [ZERO] * len(closes)
    signal_mode = str(kwargs.pop('signal_mode', signal_mode) or 'state').lower()
    fast_ma = int(kwargs.pop('fast_ma', fast_ma))
    slow_ma = int(kwargs.pop('slow_ma', slow_ma))
    rsi_len = int(kwargs.pop('rsi_len', rsi_len))
    breakout_len = int(kwargs.pop('breakout_len', breakout_len))
    volume_window = int(kwargs.pop('volume_window', volume_window))
    regime_window = int(kwargs.pop('regime_window', regime_window))
    min_score = float(kwargs.pop('min_score', min_score))
    kwargs.clear()

    if slow_ma <= fast_ma:
        raise ValueError('slow_ma must be > fast_ma')
    need = max(slow_ma + 2, breakout_len + 1, rsi_len + 2, volume_window)
    if len(closes) < need:
        return None

    fast = sma(closes, fast_ma)
    slow = sma(closes, slow_ma)
    i = len(closes) - 1
    prev = i - 1
    if any(x is None for x in (fast[prev], slow[prev], fast[i], slow[i])):
        return None
    bullish_cross = fast[prev] <= slow[prev] and fast[i] > slow[i]
    bearish_cross = fast[prev] >= slow[prev] and fast[i] < slow[i]
    event_fired = bullish_cross or bearish_cross

    total, parts, raw = _score(
        closes=closes, highs=highs, lows=lows, volumes=volumes, fast_ma=fast_ma, slow_ma=slow_ma,
        rsi_len=rsi_len, breakout_len=breakout_len, volume_window=volume_window, event_bonus=event_fired, regime_window=regime_window,
    )
    direction = raw['direction']
    score_value = _quant(total)
    component_summary = ', '.join(f"{k}={v:.2f}" for k, v in parts.items())
    if direction == 'FLAT':
        return None

    if signal_mode == 'event':
        if not event_fired or score_value < min_score:
            return None
        reason_bits = []
        if parts['rsi'] >= 10:
            reason_bits.append('RSI confirmation')
        if parts['breakout'] > 0:
            reason_bits.append('breakout confirmation')
        if parts['volume'] >= 5:
            reason_bits.append('volume expansion')
        if parts['trend'] >= 15:
            reason_bits.append('trend alignment')
        rationale = (
            f"{'Bullish' if direction == 'LONG' else 'Bearish'} Trading Brain event scored {score_value:.2f}/100. "
            + ('; '.join(reason_bits) if reason_bits else 'composite alignment detected')
            + f". Regime: {raw['regime']}. Components: {component_summary}."
        )
        return StrategySignal(direction=direction, score=score_value, rationale=rationale, signal_kind='EVENT', signal_label=_event_label(direction), score_components={**parts, 'regime_bonus': {'TRENDING': 5.0, 'SIDEWAYS': 3.0, 'VOLATILE': 2.0, 'QUIET': 1.0, 'UNKNOWN': 0.0}.get(raw['regime'], 0.0)})

    if score_value < min_score:
        return None
    reason_bits = [f"regime={raw['regime'].lower()}"]
    if parts['rsi'] >= 10:
        reason_bits.append('RSI')
    if parts['breakout'] > 0:
        reason_bits.append('breakout')
    if parts['volume'] >= 5:
        reason_bits.append('volume spike')
    if parts['trend'] >= 15:
        reason_bits.append('trend')
    if parts['momentum'] >= 10:
        reason_bits.append('momentum')
    rationale = (
        f"{'Bullish' if direction == 'LONG' else 'Bearish'} Trading Brain state scored {score_value:.2f}/100. "
        + ('Signals: ' + ', '.join(reason_bits) if reason_bits else 'Composite context available')
        + f". RSI={raw['cur_rsi']:.2f}; volume_ratio={raw['volume_ratio']:.2f}; components: {component_summary}."
    )
    return StrategySignal(direction=direction, score=score_value, rationale=rationale, signal_kind='STATE', signal_label=_state_label(direction), score_components=parts)


@register_diagnostics('trading_brain')
def trading_brain_diagnostics(
    *, closes: list[Decimal], highs: list[Decimal] | None = None, lows: list[Decimal] | None = None,
    volumes: list[Decimal] | None = None, fast_ma: int = 5, slow_ma: int = 10, rsi_len: int = 14,
    breakout_len: int = 20, volume_window: int = 20, regime_window: int = 20, signal_mode: str = 'state', min_score: float = 50.0, **kwargs,
) -> str:
    highs = highs or closes
    lows = lows or closes
    volumes = volumes or [ZERO] * len(closes)
    signal_mode = str(kwargs.pop('signal_mode', signal_mode) or 'state').lower()
    fast_ma = int(kwargs.pop('fast_ma', fast_ma))
    slow_ma = int(kwargs.pop('slow_ma', slow_ma))
    rsi_len = int(kwargs.pop('rsi_len', rsi_len))
    breakout_len = int(kwargs.pop('breakout_len', breakout_len))
    volume_window = int(kwargs.pop('volume_window', volume_window))
    regime_window = int(kwargs.pop('regime_window', regime_window))
    min_score = float(kwargs.pop('min_score', min_score))
    kwargs.clear()

    need = max(slow_ma + 2, breakout_len + 1, rsi_len + 2, volume_window)
    if len(closes) < need:
        return f'insufficient bars: need at least {need}, have {len(closes)}'

    fast = sma(closes, fast_ma)
    slow = sma(closes, slow_ma)
    i = len(closes) - 1
    prev = i - 1
    if any(x is None for x in (fast[prev], slow[prev], fast[i], slow[i])):
        return 'moving-average values unavailable on the latest bars'
    bullish_cross = fast[prev] <= slow[prev] and fast[i] > slow[i]
    bearish_cross = fast[prev] >= slow[prev] and fast[i] < slow[i]
    total, parts, raw = _score(
        closes=closes, highs=highs, lows=lows, volumes=volumes, fast_ma=fast_ma, slow_ma=slow_ma,
        rsi_len=rsi_len, breakout_len=breakout_len, volume_window=volume_window, event_bonus=(bullish_cross or bearish_cross), regime_window=regime_window,
    )
    score_value = _quant(total)
    direction = raw['direction']
    event_txt = 'bullish_cross' if bullish_cross else 'bearish_cross' if bearish_cross else 'no_fresh_cross'
    regime_txt = str(raw.get('regime', 'UNKNOWN')).lower()
    return (
        f"direction={direction}; mode={signal_mode}; event={event_txt}; score={score_value:.2f}/100; min_score={min_score:.2f}; "
        f"rsi={raw['cur_rsi']:.2f}; volume_ratio={raw['volume_ratio']:.2f}; atr_pct={float(raw['atr_pct']*100):.2f}%; components={parts}"
    )
