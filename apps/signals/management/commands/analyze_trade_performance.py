from django.core.management.base import BaseCommand
from django.db.models import Avg, Count, Q

from apps.signals.models import PaperTrade, SignalOutcome


BUCKETS = [(0, 39), (40, 59), (60, 79), (80, 100)]


def bucket_label(lo, hi):
    return f"{lo}–{hi}"


def in_bucket(score, lo, hi):
    return score is not None and lo <= float(score) <= hi


class Command(BaseCommand):
    help = "Analyze paper trade and model outcome performance by score bucket."

    def add_arguments(self, parser):
        parser.add_argument("--username", type=str, required=False)

    def handle(self, *args, **options):
        username = options.get("username")
        paper_qs = PaperTrade.objects.select_related("signal")
        if username:
            paper_qs = paper_qs.filter(opened_by__username=username)
        paper_qs = paper_qs.filter(status=PaperTrade.Status.CLOSED)

        self.stdout.write(f"Paper trade score analytics for {username or 'all users'}")
        self.stdout.write("Bucket | Trades | Wins | Losses | Win rate | Avg PnL %")
        for lo, hi in BUCKETS:
            trades = [t for t in paper_qs if in_bucket(t.signal.score, lo, hi)]
            total = len(trades)
            wins = len([t for t in trades if (t.pnl_amount or 0) > 0])
            losses = len([t for t in trades if (t.pnl_amount or 0) < 0])
            avg = (sum((t.pnl_pct or 0) for t in trades) / total) if total else None
            win_rate = (wins / total * 100) if total else 0
            avg_text = f"{avg:.2f}%" if avg is not None else "—"
            self.stdout.write(f"{bucket_label(lo, hi):>6} | {total:>6} | {wins:>4} | {losses:>6} | {win_rate:>8.2f}% | {avg_text}")

        outcome_qs = SignalOutcome.objects.select_related("signal").filter(status=SignalOutcome.Status.EVALUATED)
        if username:
            outcome_qs = outcome_qs.filter(signal__created_by__username=username)
        self.stdout.write("\nModel outcome bucket analytics")
        self.stdout.write("Bucket | Signals | Wins | Losses | Win rate | Avg return %")
        for lo, hi in BUCKETS:
            items = [o for o in outcome_qs if in_bucket(o.signal.score, lo, hi)]
            total = len(items)
            wins = len([o for o in items if o.outcome_label == SignalOutcome.OutcomeLabel.WIN])
            losses = len([o for o in items if o.outcome_label == SignalOutcome.OutcomeLabel.LOSS])
            avg = (sum((o.return_pct or 0) for o in items) / total) if total else None
            win_rate = (wins / total * 100) if total else 0
            avg_text = f"{avg:.2f}%" if avg is not None else "—"
            self.stdout.write(f"{bucket_label(lo, hi):>6} | {total:>7} | {wins:>4} | {losses:>6} | {win_rate:>8.2f}% | {avg_text}")
