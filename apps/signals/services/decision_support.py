from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.portfolios.models import HeldPosition

from ..models import Signal


TERMINAL_SIGNAL_STATUSES = {
    Signal.Status.SKIPPED,
    Signal.Status.CLOSED_WIN,
    Signal.Status.CLOSED_LOSS,
    Signal.Status.REJECTED,
    Signal.Status.EXPIRED,
    Signal.Status.ARCHIVED,
}


@dataclass(frozen=True)
class SignalDecisionSnapshot:
    code: str
    label: str
    reason: str
    next_step: str
    rank: int
    bucket: str


def normalize_signal_score(score: float | Decimal | None) -> float | None:
    if score is None:
        return None
    value = float(score)
    if value <= 1:
        value *= 100
    return max(0.0, min(100.0, value))


def assess_signal_action(
    *,
    signal: Signal,
    guardrails: dict[str, Any] | None,
    entry_price: Decimal | float | None,
    suggested_qty: int | None,
    has_open_position: bool = False,
) -> SignalDecisionSnapshot:
    """Translate raw signal state into an operator-friendly action recommendation."""
    score = normalize_signal_score(getattr(signal, "score", None))
    posture = (guardrails or {}).get("overall_posture") or "UNKNOWN"
    posture_label = (guardrails or {}).get("overall_label") or "guardrails unavailable"

    if signal.status in TERMINAL_SIGNAL_STATUSES:
        return SignalDecisionSnapshot(
            code="DONE",
            label="Done",
            reason=f"Signal status is {signal.get_status_display().lower()}, so it is no longer an active trade candidate.",
            next_step="Review history only; no new action needed.",
            rank=99,
            bucket="done",
        )

    if signal.direction == Signal.Direction.FLAT:
        return SignalDecisionSnapshot(
            code="WAIT",
            label="Wait",
            reason="Flat signals are informational state changes, not direct long or short entries.",
            next_step="Monitor for a directional setup before taking action.",
            rank=80,
            bucket="wait",
        )

    if has_open_position:
        return SignalDecisionSnapshot(
            code="HOLDING",
            label="Already held",
            reason="You already have an open held position for this symbol, so a new entry would duplicate exposure.",
            next_step="Review the existing holding instead of treating this as a fresh buy.",
            rank=70,
            bucket="owned",
        )

    if not getattr(signal, "trade_plan", None):
        return SignalDecisionSnapshot(
            code="PLAN_FIRST",
            label="Plan first",
            reason="This signal does not have a trade plan yet, so the app cannot size or risk-check it confidently.",
            next_step="Generate or record entry, stop, target, and size before acting.",
            rank=60,
            bucket="setup",
        )

    if entry_price is None or suggested_qty in (None, 0):
        return SignalDecisionSnapshot(
            code="REVIEW_SETUP",
            label="Review setup",
            reason="Entry price or suggested size is missing, so the trade is not actionable yet.",
            next_step="Confirm pricing and position size before acting.",
            rank=55,
            bucket="setup",
        )

    if posture == "OVER":
        return SignalDecisionSnapshot(
            code="SKIP_RISK",
            label="Skip — risk cap",
            reason=f"Current allocation posture is {posture_label.lower()}, so this trade would violate present guardrails.",
            next_step="Skip for now or reduce exposure until the guardrails clear.",
            rank=50,
            bucket="blocked",
        )

    if score is None:
        return SignalDecisionSnapshot(
            code="REVIEW_UNSCORED",
            label="Manual review",
            reason="The signal is unscored, so it still needs operator judgment before acting.",
            next_step="Read the rationale and confirm whether the setup deserves a plan.",
            rank=45,
            bucket="review",
        )

    age_hours: float | None = None
    if getattr(signal, "generated_at", None) is not None:
        try:
            age_hours = (timezone.now() - signal.generated_at).total_seconds() / 3600
        except Exception:
            pass

    if score >= 85 and posture == "OK":
        if age_hours is not None and age_hours >= 48:
            return SignalDecisionSnapshot(
                code="WATCH_CLOSE",
                label="Watch closely",
                reason=f"Strong score but the signal is {int(age_hours)}h old — staleness reduces entry urgency.",
                next_step="Confirm the chart setup is still valid before acting.",
                rank=20,
                bucket="watch",
            )
        return SignalDecisionSnapshot(
            code="BUY_NOW",
            label="Buy now",
            reason="High score plus clean guardrails makes this the strongest current entry candidate.",
            next_step="Open a paper trade or real position if the thesis still matches the chart.",
            rank=10,
            bucket="action",
        )

    if score >= 75 and posture in {"OK", "NEAR"}:
        if age_hours is not None and age_hours >= 72:
            return SignalDecisionSnapshot(
                code="REVIEW",
                label="Review",
                reason=f"Good score but the signal is {int(age_hours)}h old — confirm the thesis is still valid.",
                next_step="Re-examine the chart and decide whether the setup has decayed.",
                rank=30,
                bucket="review",
            )
        return SignalDecisionSnapshot(
            code="WATCH_CLOSE",
            label="Watch closely",
            reason="This setup is strong enough to stay near the top of the queue but still needs final confirmation.",
            next_step="Recheck chart structure and risk fit before entering.",
            rank=20,
            bucket="watch",
        )

    if score >= 60:
        return SignalDecisionSnapshot(
            code="REVIEW",
            label="Review",
            reason="The score is in the review band, so the setup is worth inspection but not an immediate entry.",
            next_step="Read the rationale, inspect the chart, and decide whether to wait or skip.",
            rank=30,
            bucket="review",
        )

    return SignalDecisionSnapshot(
        code="PASS",
        label="Pass",
        reason="Relative conviction is low versus other current setups.",
        next_step="Keep it in history, but focus attention on stronger names first.",
        rank=40,
        bucket="pass",
    )


def build_signal_decision_summary(rows: list[SignalDecisionSnapshot]) -> dict[str, int]:
    summary = {
        "BUY_NOW": 0,
        "WATCH_CLOSE": 0,
        "REVIEW": 0,
        "SKIP_RISK": 0,
        "HOLDING": 0,
        "OTHER": 0,
    }
    for row in rows:
        if row.code in summary:
            summary[row.code] += 1
        else:
            summary["OTHER"] += 1
    return summary


def has_open_position_for_symbol(*, user, instrument_id: int) -> bool:
    return HeldPosition.objects.filter(
        user=user,
        instrument_id=instrument_id,
        status=HeldPosition.Status.OPEN,
    ).exists()
