from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from django.db.models import Avg, Count, Q

from apps.signals.models import PaperTrade, SignalOutcome


@dataclass(frozen=True)
class ScoreBucketRow:
    label: str
    min_score: float
    max_score: float
    total: int
    wins: int
    losses: int
    open_count: int
    win_rate: float
    avg_pnl_pct: float | None
    avg_score: float | None


BUCKETS = [
    (0.0, 39.99, "0–39"),
    (40.0, 59.99, "40–59"),
    (60.0, 79.99, "60–79"),
    (80.0, 100.0, "80–100"),
]


def _safe_mean(values: list[float]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    return round(mean(vals), 2)


def compute_score_trade_analytics(*, user) -> dict:
    closed_qs = PaperTrade.objects.select_related("signal").filter(user=user)
    rows: list[ScoreBucketRow] = []
    for min_score, max_score, label in BUCKETS:
        bucket = closed_qs.filter(signal__score__gte=min_score, signal__score__lte=max_score)
        total = bucket.count()
        wins = bucket.filter(status=PaperTrade.Status.CLOSED, pnl__gt=0).count()
        losses = bucket.filter(status=PaperTrade.Status.CLOSED, pnl__lte=0).count()
        open_count = bucket.filter(status=PaperTrade.Status.OPEN).count()
        realized = bucket.filter(status=PaperTrade.Status.CLOSED)
        pnl_vals = list(realized.values_list("pnl_pct", flat=True))
        score_vals = list(bucket.values_list("signal__score", flat=True))
        win_rate = round((wins / max(wins + losses, 1)) * 100, 2) if (wins + losses) else 0.0
        rows.append(
            ScoreBucketRow(
                label=label,
                min_score=min_score,
                max_score=max_score,
                total=total,
                wins=wins,
                losses=losses,
                open_count=open_count,
                win_rate=win_rate,
                avg_pnl_pct=_safe_mean(pnl_vals),
                avg_score=_safe_mean(score_vals),
            )
        )

    closed_realized = closed_qs.filter(status=PaperTrade.Status.CLOSED)
    best_bucket = None
    populated = [r for r in rows if (r.wins + r.losses) > 0]
    if populated:
        best_bucket = max(populated, key=lambda r: (r.win_rate, r.avg_pnl_pct or -999))

    return {
        "rows": rows,
        "closed_trade_count": closed_realized.count(),
        "best_bucket": best_bucket,
    }


def compute_model_outcome_analytics() -> dict:
    qs = SignalOutcome.objects.select_related("signal")
    rows = []
    for min_score, max_score, label in BUCKETS:
        bucket = qs.filter(signal__score__gte=min_score, signal__score__lte=max_score)
        total = bucket.count()
        wins = bucket.filter(outcome_label=SignalOutcome.OutcomeLabel.WIN).count()
        losses = bucket.filter(outcome_label=SignalOutcome.OutcomeLabel.LOSS).count()
        open_count = bucket.filter(Q(status=SignalOutcome.Status.PENDING) | Q(outcome_label=SignalOutcome.OutcomeLabel.OPEN)).count()
        return_vals = list(bucket.values_list("return_pct", flat=True))
        score_vals = list(bucket.values_list("signal__score", flat=True))
        win_rate = round((wins / max(wins + losses, 1)) * 100, 2) if (wins + losses) else 0.0
        rows.append(
            ScoreBucketRow(
                label=label,
                min_score=min_score,
                max_score=max_score,
                total=total,
                wins=wins,
                losses=losses,
                open_count=open_count,
                win_rate=win_rate,
                avg_pnl_pct=_safe_mean(return_vals),
                avg_score=_safe_mean(score_vals),
            )
        )
    return {"rows": rows, "evaluated_count": qs.exclude(status=SignalOutcome.Status.PENDING).count()}
