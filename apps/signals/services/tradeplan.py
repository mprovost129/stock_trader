"""Trade plan builder.

Milestone 1: create a reasonable, *alerts-friendly* trade plan from stored bars.
No execution. Conservative defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from apps.marketdata.models import PriceBar
from apps.marketdata.services.indicators import OHLCV, atr
from apps.portfolios.models import UserRiskProfile
from apps.signals.models import Signal, TradePlan
from apps.signals.services.planner import suggested_qty


@dataclass(frozen=True)
class PlanInputs:
    entry: Decimal
    stop: Decimal
    target_1: Decimal
    target_2: Decimal
    notes: str


def _decimal(v: Decimal) -> Decimal:
    # normalize quantization later; DB will store with decimal_places=8
    return v


def build_plan_inputs(*, signal: Signal, atr_mult: Decimal = Decimal("2.0")) -> PlanInputs | None:
    """Build entry/stop/targets using last close + ATR on the signal timeframe.

    LONG:
      entry = last close
      stop  = entry - atr_mult * ATR(14)
      targets = entry + 1R, entry + 2R

    SHORT:
      entry = last close
      stop  = entry + atr_mult * ATR(14)
      targets = entry - 1R, entry - 2R
    """

    bars_qs = (
        PriceBar.objects.filter(instrument=signal.instrument, timeframe=signal.timeframe)
        .order_by("ts")
        .only("ts", "open", "high", "low", "close", "volume")
    )
    bars = list(bars_qs)
    if len(bars) < 20:
        return None

    ohlcv = [OHLCV(ts=b.ts, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume) for b in bars]
    atr_series = atr(ohlcv, length=14)
    cur_atr = atr_series[-1]
    last = bars[-1]
    entry = last.close

    # Fallback if ATR not available for some reason:
    if cur_atr is None or cur_atr <= 0:
        # 2% fallback stop distance (very conservative)
        cur_atr = max(entry * Decimal("0.02"), Decimal("0.01"))

    stop_dist = cur_atr * atr_mult

    if signal.direction == Signal.Direction.LONG:
        stop = entry - stop_dist
        r = entry - stop
        t1 = entry + r
        t2 = entry + (r * Decimal("2"))
    elif signal.direction == Signal.Direction.SHORT:
        stop = entry + stop_dist
        r = stop - entry
        t1 = entry - r
        t2 = entry - (r * Decimal("2"))
    else:
        return None

    notes = f"ATR14={cur_atr:.8f}, atr_mult={atr_mult} (stop-first sizing)"
    return PlanInputs(entry=_decimal(entry), stop=_decimal(stop), target_1=_decimal(t1), target_2=_decimal(t2), notes=notes)


def ensure_trade_plan(signal: Signal, *, user=None, atr_mult: Decimal = Decimal("2.0")) -> TradePlan | None:
    """Create or return existing TradePlan for a signal."""
    if hasattr(signal, "trade_plan"):
        return signal.trade_plan

    inputs = build_plan_inputs(signal=signal, atr_mult=atr_mult)
    if inputs is None:
        return None

    account_equity: Decimal | None = None
    risk_pct: Decimal = Decimal("0.0025")  # default 0.25%

    if user is not None:
        try:
            rp = UserRiskProfile.objects.get(user=user)
            account_equity = rp.account_equity
            risk_pct = rp.risk_per_trade_pct
        except UserRiskProfile.DoesNotExist:
            pass

    suggested = None
    if account_equity is not None and account_equity > 0:
        suggested = suggested_qty(account_equity=account_equity, risk_pct=risk_pct, entry=inputs.entry, stop=inputs.stop)

    with transaction.atomic():
        plan = TradePlan.objects.create(
            signal=signal,
            entry_price=inputs.entry,
            stop_price=inputs.stop,
            target_1=inputs.target_1,
            target_2=inputs.target_2,
            account_equity=account_equity,
            risk_per_trade_pct=risk_pct,
            suggested_qty=suggested,
            notes=inputs.notes,
        )
    return plan
