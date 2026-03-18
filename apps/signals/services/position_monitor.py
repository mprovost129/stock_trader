from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.marketdata.models import PriceBar
from apps.signals.models import PaperTrade, PositionAlert, Signal
from apps.signals.services.alerts import _post_discord




@dataclass(frozen=True)
class OpenPositionSnapshot:
    trade: PaperTrade
    current_price: Decimal | None
    pnl_pct: float | None
    stop_distance_pct: float | None
    risk_score: float
    headline: str


def build_open_position_snapshot(trade: PaperTrade) -> OpenPositionSnapshot:
    bar = (
        PriceBar.objects.filter(instrument=trade.signal.instrument, timeframe=trade.signal.timeframe)
        .order_by("-ts")
        .first()
    )
    if not bar:
        return OpenPositionSnapshot(trade=trade, current_price=None, pnl_pct=None, stop_distance_pct=None, risk_score=0.0, headline="No current bar available")

    current = Decimal(bar.close)
    entry = Decimal(trade.entry_price)
    if trade.signal.direction == Signal.Direction.SHORT:
        pnl_pct = float(((entry - current) / entry) * Decimal("100")) if entry else None
    else:
        pnl_pct = float(((current - entry) / entry) * Decimal("100")) if entry else None

    stop_distance_pct = None
    risk_score = 0.0
    headline = "Stable"
    plan = getattr(trade.signal, "trade_plan", None)
    if pnl_pct is not None and pnl_pct < 0:
        risk_score += abs(pnl_pct) * 10
        headline = f"Underwater {pnl_pct:.2f}%"
    if plan and plan.stop_price is not None and current > 0:
        stop = Decimal(plan.stop_price)
        stop_distance_pct = abs(float(((current - stop) / current) * Decimal("100")))
        stop_threshold = float(getattr(settings, "POSITION_STOP_ALERT_DISTANCE_PCT", 1.0) or 1.0)
        if stop_distance_pct <= abs(stop_threshold):
            risk_score += (abs(stop_threshold) - stop_distance_pct + 1) * 40
            headline = f"{stop_distance_pct:.2f}% from stop"
    latest_reversal = (
        Signal.objects.filter(
            instrument=trade.signal.instrument,
            strategy=trade.signal.strategy,
            timeframe=trade.signal.timeframe,
            generated_at__gte=trade.entry_time,
        )
        .exclude(pk=trade.signal_id)
        .exclude(direction__in=[Signal.Direction.FLAT, trade.signal.direction])
        .order_by("-generated_at", "-id")
        .first()
    )
    if latest_reversal:
        risk_score += 50
        headline = f"Trend flipped to {latest_reversal.direction}"

    return OpenPositionSnapshot(
        trade=trade,
        current_price=current,
        pnl_pct=round(pnl_pct, 4) if pnl_pct is not None else None,
        stop_distance_pct=round(stop_distance_pct, 4) if stop_distance_pct is not None else None,
        risk_score=round(risk_score, 2),
        headline=headline,
    )


def rank_open_positions(*, username: str | None = None, limit: int = 5) -> list[OpenPositionSnapshot]:
    qs = PaperTrade.objects.select_related("signal", "signal__instrument", "signal__strategy", "signal__trade_plan").filter(status=PaperTrade.Status.OPEN)
    if username:
        qs = qs.filter(opened_by__username=username)
    snapshots = [build_open_position_snapshot(trade) for trade in qs]
    snapshots.sort(key=lambda item: (-item.risk_score, item.trade.updated_at.timestamp() * -1))
    return snapshots[:limit]


@dataclass(frozen=True)
class MonitorResult:
    trade_id: int
    symbol: str
    alert_type: str | None
    status: str
    message: str


def evaluate_open_trade(trade: PaperTrade) -> list[tuple[str, str]]:
    bar = (
        PriceBar.objects.filter(instrument=trade.signal.instrument, timeframe=trade.signal.timeframe)
        .order_by("-ts")
        .first()
    )
    if not bar:
        return []

    findings: list[tuple[str, str]] = []
    current = Decimal(bar.close)
    entry = Decimal(trade.entry_price)
    if trade.signal.direction == Signal.Direction.SHORT:
        pnl_pct = float(((entry - current) / entry) * Decimal("100")) if entry else 0.0
    else:
        pnl_pct = float(((current - entry) / entry) * Decimal("100")) if entry else 0.0

    deterioration_threshold = float(getattr(settings, "POSITION_DETERIORATION_ALERT_PCT", 2.0) or 2.0)
    if pnl_pct <= -abs(deterioration_threshold):
        findings.append((PositionAlert.AlertType.DETERIORATING, f"Unrealized P&L has deteriorated to {pnl_pct:.2f}%"))

    plan = getattr(trade.signal, "trade_plan", None)
    if plan and plan.stop_price is not None and current > 0:
        stop = Decimal(plan.stop_price)
        distance_pct = abs(float(((current - stop) / current) * Decimal("100")))
        stop_threshold = float(getattr(settings, "POSITION_STOP_ALERT_DISTANCE_PCT", 1.0) or 1.0)
        if distance_pct <= abs(stop_threshold):
            findings.append((PositionAlert.AlertType.STOP_APPROACHING, f"Price is {distance_pct:.2f}% from stop ({stop})."))

    latest_signal = (
        Signal.objects.filter(
            instrument=trade.signal.instrument,
            strategy=trade.signal.strategy,
            timeframe=trade.signal.timeframe,
            generated_at__gte=trade.entry_time,
        )
        .exclude(pk=trade.signal_id)
        .order_by("-generated_at", "-id")
        .first()
    )
    if latest_signal and latest_signal.direction not in {Signal.Direction.FLAT, trade.signal.direction}:
        findings.append((PositionAlert.AlertType.TREND_REVERSAL, f"Latest signal flipped to {latest_signal.direction} ({latest_signal.signal_label or latest_signal.signal_kind})."))

    return findings


def monitor_open_positions(*, username: str | None = None, dry_run: bool = False) -> list[MonitorResult]:
    qs = PaperTrade.objects.select_related("signal", "signal__instrument", "signal__strategy", "signal__trade_plan").filter(status=PaperTrade.Status.OPEN)
    if username:
        qs = qs.filter(opened_by__username=username)
    results: list[MonitorResult] = []
    cooldown_minutes = int(getattr(settings, "POSITION_ALERT_COOLDOWN_MINUTES", 120) or 120)
    webhook_url = getattr(settings, "DISCORD_WEBHOOK_URL", "").strip()

    for trade in qs:
        findings = evaluate_open_trade(trade)
        if not findings:
            results.append(MonitorResult(trade.id, trade.signal.instrument.symbol, None, "ok", "no position risk findings"))
            continue
        for alert_type, message in findings:
            recent = trade.position_alerts.filter(alert_type=alert_type, created_at__gte=timezone.now() - timedelta(minutes=cooldown_minutes)).exists()
            if recent:
                results.append(MonitorResult(trade.id, trade.signal.instrument.symbol, alert_type, "skipped", "cooldown active"))
                continue
            payload = build_position_alert_payload(trade=trade, alert_type=alert_type, message=message)
            if dry_run:
                PositionAlert.objects.create(paper_trade=trade, alert_type=alert_type, status=PositionAlert.Status.DRY_RUN, reason="dry_run", payload_snapshot=payload)
                results.append(MonitorResult(trade.id, trade.signal.instrument.symbol, alert_type, "dry_run", message))
                continue
            if not webhook_url:
                PositionAlert.objects.create(paper_trade=trade, alert_type=alert_type, status=PositionAlert.Status.FAILED, reason="missing_webhook", payload_snapshot=payload, error_message="DISCORD_WEBHOOK_URL is not configured." )
                results.append(MonitorResult(trade.id, trade.signal.instrument.symbol, alert_type, "failed", "missing webhook"))
                continue
            try:
                _post_discord(webhook_url=webhook_url, payload=payload)
                PositionAlert.objects.create(paper_trade=trade, alert_type=alert_type, status=PositionAlert.Status.SENT, reason="sent", payload_snapshot=payload, delivered_at=timezone.now())
                results.append(MonitorResult(trade.id, trade.signal.instrument.symbol, alert_type, "sent", message))
            except Exception as exc:  # noqa: BLE001
                PositionAlert.objects.create(paper_trade=trade, alert_type=alert_type, status=PositionAlert.Status.FAILED, reason="exception", payload_snapshot=payload, error_message=str(exc))
                results.append(MonitorResult(trade.id, trade.signal.instrument.symbol, alert_type, "failed", str(exc)))
    return results


def build_position_alert_payload(*, trade: PaperTrade, alert_type: str, message: str) -> dict:
    title_map = {
        PositionAlert.AlertType.DETERIORATING: "⚠ Position deteriorating",
        PositionAlert.AlertType.STOP_APPROACHING: "⚠ Stop approaching",
        PositionAlert.AlertType.TREND_REVERSAL: "⚠ Trend reversal",
    }
    return {
        "content": f"{title_map.get(alert_type, 'Position alert')} — {trade.signal.instrument.symbol}",
        "embeds": [
            {
                "title": f"{trade.signal.instrument.symbol} {trade.signal.direction} position monitor",
                "description": message,
                "color": 0xF39C12,
                "fields": [
                    {"name": "Entry", "value": str(trade.entry_price), "inline": True},
                    {"name": "Status", "value": trade.status, "inline": True},
                    {"name": "Signal", "value": trade.signal.signal_label or trade.signal.signal_kind, "inline": True},
                ],
                "footer": {"text": "Trading Advisor — manual execution / position monitoring"},
            }
        ],
    }
