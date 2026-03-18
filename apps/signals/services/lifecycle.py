from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from apps.marketdata.models import PriceBar
from apps.signals.models import PaperTrade, Signal


@dataclass(frozen=True)
class LifecycleSyncResult:
    trade: PaperTrade
    changed: bool
    headline: str


def _calc_pnl_pct(*, direction: str, entry: Decimal, current: Decimal) -> float | None:
    if not entry:
        return None
    if direction == Signal.Direction.SHORT:
        return float(((entry - current) / entry) * Decimal("100"))
    return float(((current - entry) / entry) * Decimal("100"))



def _price_bar_for_trade(trade: PaperTrade):
    return (
        PriceBar.objects.filter(instrument=trade.signal.instrument, timeframe=trade.signal.timeframe)
        .order_by("-ts")
        .first()
    )



def sync_trade_lifecycle(trade: PaperTrade) -> LifecycleSyncResult:
    if trade.status == PaperTrade.Status.CLOSED:
        if trade.lifecycle_stage != PaperTrade.LifecycleStage.CLOSED:
            trade.lifecycle_stage = PaperTrade.LifecycleStage.CLOSED
            trade.save(update_fields=["lifecycle_stage", "updated_at"])
            return LifecycleSyncResult(trade=trade, changed=True, headline="Marked closed trade as CLOSED stage")
        return LifecycleSyncResult(trade=trade, changed=False, headline="Trade already closed")

    bar = _price_bar_for_trade(trade)
    if not bar:
        return LifecycleSyncResult(trade=trade, changed=False, headline="No latest price bar available")

    current = Decimal(bar.close)
    entry = Decimal(trade.entry_price)
    direction = trade.signal.direction
    plan = getattr(trade.signal, "trade_plan", None)

    changed_fields: list[str] = []
    headline_parts: list[str] = []

    if trade.last_price != current:
        trade.last_price = current
        trade.last_price_at = bar.ts
        changed_fields.extend(["last_price", "last_price_at"])

    if trade.highest_price_seen is None or current > trade.highest_price_seen:
        trade.highest_price_seen = current
        changed_fields.append("highest_price_seen")
    if trade.lowest_price_seen is None or current < trade.lowest_price_seen:
        trade.lowest_price_seen = current
        changed_fields.append("lowest_price_seen")

    pnl_pct = _calc_pnl_pct(direction=direction, entry=entry, current=current)
    pnl_amount = None
    qty = trade.quantity or 1
    if direction == Signal.Direction.SHORT:
        pnl_amount = (entry - current) * Decimal(qty)
    else:
        pnl_amount = (current - entry) * Decimal(qty)
    pnl_amount = pnl_amount.quantize(Decimal("0.01"))
    if trade.pnl_amount != pnl_amount:
        trade.pnl_amount = pnl_amount
        changed_fields.append("pnl_amount")
    if trade.pnl_pct != (round(pnl_pct, 4) if pnl_pct is not None else None):
        trade.pnl_pct = round(pnl_pct, 4) if pnl_pct is not None else None
        changed_fields.append("pnl_pct")

    stop = Decimal(plan.stop_price) if plan and plan.stop_price is not None else None
    target_1 = Decimal(plan.target_1) if plan and plan.target_1 is not None else None
    target_2 = Decimal(plan.target_2) if plan and plan.target_2 is not None else None

    active_stop = Decimal(trade.active_stop_price) if trade.active_stop_price is not None else stop
    active_target = Decimal(trade.active_target_price) if trade.active_target_price is not None else target_1

    if trade.active_stop_price is None and active_stop is not None:
        trade.active_stop_price = active_stop
        changed_fields.append("active_stop_price")
    if trade.active_target_price is None and active_target is not None:
        trade.active_target_price = active_target
        changed_fields.append("active_target_price")

    if trade.trailing_stop_pct and trade.trailing_stop_pct > 0:
        trailing = Decimal(trade.trailing_stop_pct) / Decimal("100")
        if direction == Signal.Direction.LONG and trade.highest_price_seen:
            candidate_stop = Decimal(trade.highest_price_seen) * (Decimal("1") - trailing)
            if trade.active_stop_price is None or candidate_stop > Decimal(trade.active_stop_price):
                trade.active_stop_price = candidate_stop
                changed_fields.append("active_stop_price")
                headline_parts.append("trailing stop raised")
        elif direction == Signal.Direction.SHORT and trade.lowest_price_seen:
            candidate_stop = Decimal(trade.lowest_price_seen) * (Decimal("1") + trailing)
            if trade.active_stop_price is None or candidate_stop < Decimal(trade.active_stop_price):
                trade.active_stop_price = candidate_stop
                changed_fields.append("active_stop_price")
                headline_parts.append("trailing stop lowered")

    stage = PaperTrade.LifecycleStage.ACTIVE
    if trade.stop_triggered:
        stage = PaperTrade.LifecycleStage.STOP_RISK
    if direction == Signal.Direction.LONG:
        if target_1 is not None and current >= target_1 and not trade.target_1_hit:
            trade.target_1_hit = True
            changed_fields.append("target_1_hit")
            headline_parts.append("target 1 hit")
        if target_2 is not None and current >= target_2 and not trade.target_2_hit:
            trade.target_2_hit = True
            changed_fields.append("target_2_hit")
            headline_parts.append("target 2 hit")
        if trade.active_stop_price is not None and current <= Decimal(trade.active_stop_price) and not trade.stop_triggered:
            trade.stop_triggered = True
            changed_fields.append("stop_triggered")
            headline_parts.append("stop touched")
    elif direction == Signal.Direction.SHORT:
        if target_1 is not None and current <= target_1 and not trade.target_1_hit:
            trade.target_1_hit = True
            changed_fields.append("target_1_hit")
            headline_parts.append("target 1 hit")
        if target_2 is not None and current <= target_2 and not trade.target_2_hit:
            trade.target_2_hit = True
            changed_fields.append("target_2_hit")
            headline_parts.append("target 2 hit")
        if trade.active_stop_price is not None and current >= Decimal(trade.active_stop_price) and not trade.stop_triggered:
            trade.stop_triggered = True
            changed_fields.append("stop_triggered")
            headline_parts.append("stop touched")

    if trade.stop_triggered:
        stage = PaperTrade.LifecycleStage.STOP_RISK
    elif trade.target_2_hit:
        stage = PaperTrade.LifecycleStage.EXIT_READY
        if trade.active_target_price != target_2 and target_2 is not None:
            trade.active_target_price = target_2
            changed_fields.append("active_target_price")
    elif trade.target_1_hit:
        stage = PaperTrade.LifecycleStage.TARGET_1
        if plan and plan.entry_price is not None and trade.active_stop_price != plan.entry_price:
            trade.active_stop_price = plan.entry_price
            changed_fields.append("active_stop_price")
            headline_parts.append("stop moved to breakeven")
        if target_2 is not None and trade.active_target_price != target_2:
            trade.active_target_price = target_2
            changed_fields.append("active_target_price")
    elif pnl_pct is not None and pnl_pct < 0:
        stage = PaperTrade.LifecycleStage.STOP_RISK

    if trade.lifecycle_stage != stage:
        trade.lifecycle_stage = stage
        changed_fields.append("lifecycle_stage")

    if changed_fields:
        trade.save(update_fields=sorted(set(changed_fields + ["updated_at"])))
    headline = ", ".join(headline_parts) if headline_parts else f"Stage {trade.lifecycle_stage.lower()}"
    return LifecycleSyncResult(trade=trade, changed=bool(changed_fields), headline=headline)



def sync_open_trade_lifecycles(limit: int | None = None) -> list[LifecycleSyncResult]:
    qs = PaperTrade.objects.select_related("signal", "signal__instrument", "signal__trade_plan").filter(status=PaperTrade.Status.OPEN).order_by("-updated_at", "-id")
    if limit:
        qs = qs[:limit]
    return [sync_trade_lifecycle(trade) for trade in qs]



def get_trade_lifecycle_summary() -> dict:
    open_qs = PaperTrade.objects.filter(status=PaperTrade.Status.OPEN)
    stage_counts = open_qs.aggregate(
        total=Count("id"),
        new=Count("id", filter=Q(lifecycle_stage=PaperTrade.LifecycleStage.NEW)),
        active=Count("id", filter=Q(lifecycle_stage=PaperTrade.LifecycleStage.ACTIVE)),
        target_1=Count("id", filter=Q(lifecycle_stage=PaperTrade.LifecycleStage.TARGET_1)),
        exit_ready=Count("id", filter=Q(lifecycle_stage=PaperTrade.LifecycleStage.EXIT_READY)),
        stop_risk=Count("id", filter=Q(lifecycle_stage=PaperTrade.LifecycleStage.STOP_RISK)),
    )
    stale_cutoff = timezone.now() - timedelta(hours=24)
    stale_count = open_qs.filter(Q(last_price_at__isnull=True) | Q(last_price_at__lt=stale_cutoff)).count()
    return {
        "total_open": stage_counts.get("total", 0),
        "new": stage_counts.get("new", 0),
        "active": stage_counts.get("active", 0),
        "target_1": stage_counts.get("target_1", 0),
        "exit_ready": stage_counts.get("exit_ready", 0),
        "stop_risk": stage_counts.get("stop_risk", 0),
        "stale_count": stale_count,
    }
