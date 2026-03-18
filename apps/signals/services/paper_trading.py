from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.marketdata.models import PriceBar
from apps.signals.models import PaperTrade, Signal


@dataclass(frozen=True)
class PaperTradeOpenResult:
    trade: PaperTrade
    created: bool


@dataclass(frozen=True)
class PaperTradeCloseResult:
    trade: PaperTrade
    realized_pnl_amount: Decimal | None
    realized_pnl_pct: float | None


def latest_price_for_signal(signal: Signal):
    bar = (
        PriceBar.objects.filter(instrument=signal.instrument, timeframe=signal.timeframe)
        .order_by("-ts")
        .first()
    )
    return bar.close if bar else None


def open_paper_trade_from_signal(*, signal: Signal, user=None, notes: str = "") -> PaperTradeOpenResult:
    trade = getattr(signal, "paper_trade", None)
    if trade is not None:
        return PaperTradeOpenResult(trade=trade, created=False)

    entry_price = None
    qty = None
    risk_amount = None
    if hasattr(signal, "trade_plan"):
        entry_price = signal.trade_plan.entry_price
        qty = signal.trade_plan.suggested_qty
        if signal.trade_plan.account_equity is not None and signal.trade_plan.risk_per_trade_pct is not None:
            risk_amount = Decimal(signal.trade_plan.account_equity) * Decimal(signal.trade_plan.risk_per_trade_pct)
    if entry_price is None:
        entry_price = latest_price_for_signal(signal) or Decimal("0")

    plan = getattr(signal, "trade_plan", None)
    trailing_stop_pct = getattr(settings, "PAPER_TRADE_DEFAULT_TRAILING_STOP_PCT", "")
    trade = PaperTrade.objects.create(
        signal=signal,
        opened_by=user,
        entry_price=entry_price,
        quantity=qty,
        risk_amount=risk_amount,
        lifecycle_stage=PaperTrade.LifecycleStage.ACTIVE,
        active_stop_price=getattr(plan, "stop_price", None),
        active_target_price=getattr(plan, "target_1", None),
        trailing_stop_pct=trailing_stop_pct or None,
        highest_price_seen=entry_price,
        lowest_price_seen=entry_price,
        last_price=entry_price,
        last_price_at=timezone.now(),
        notes=notes,
    )
    signal.status = Signal.Status.TAKEN
    signal.save(update_fields=["status"])
    return PaperTradeOpenResult(trade=trade, created=True)


def close_paper_trade(*, trade: PaperTrade, exit_price: Decimal | None = None, notes: str = "", closed_reason: str = PaperTrade.ClosedReason.MANUAL) -> PaperTradeCloseResult:
    if trade.status == PaperTrade.Status.CLOSED:
        return PaperTradeCloseResult(trade=trade, realized_pnl_amount=trade.pnl_amount, realized_pnl_pct=trade.pnl_pct)

    if exit_price is None:
        exit_price = latest_price_for_signal(trade.signal)
    if exit_price is None:
        raise ValueError("Could not determine exit price from latest market data.")

    entry = Decimal(trade.entry_price)
    exit_dec = Decimal(exit_price)
    qty = trade.quantity or 1
    if trade.signal.direction == Signal.Direction.SHORT:
        pnl_amount = (entry - exit_dec) * Decimal(qty)
        pnl_pct = float(((entry - exit_dec) / entry) * Decimal("100")) if entry else None
    else:
        pnl_amount = (exit_dec - entry) * Decimal(qty)
        pnl_pct = float(((exit_dec - entry) / entry) * Decimal("100")) if entry else None

    trade.exit_price = exit_dec
    trade.exit_time = timezone.now()
    trade.pnl_amount = pnl_amount.quantize(Decimal("0.01"))
    trade.pnl_pct = round(pnl_pct, 4) if pnl_pct is not None else None
    trade.status = PaperTrade.Status.CLOSED
    trade.notes = (trade.notes + "\n" + notes).strip() if notes else trade.notes
    trade.save()

    signal = trade.signal
    if trade.pnl_amount is not None and trade.pnl_amount >= 0:
        signal.status = Signal.Status.CLOSED_WIN
    else:
        signal.status = Signal.Status.CLOSED_LOSS
    signal.save(update_fields=["status"])

    return PaperTradeCloseResult(trade=trade, realized_pnl_amount=trade.pnl_amount, realized_pnl_pct=trade.pnl_pct)
