from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from apps.signals.models import Signal
from apps.signals.services.outcomes import evaluate_signal_outcome


class Command(BaseCommand):
    help = "Evaluate signal outcomes over a configurable lookahead window."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=False)
        parser.add_argument("--lookahead-bars", type=int, default=5)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--only-new", action="store_true", help="Evaluate only NEW signals.")
        parser.add_argument("--only-missing", action="store_true", help="Skip signals that already have fully evaluated outcomes.")

    def handle(self, *args, **options):
        qs = Signal.objects.select_related("instrument", "strategy", "trade_plan", "outcome").order_by("-generated_at")
        username = options.get("username")
        if username:
            User = get_user_model()
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist as exc:
                raise CommandError(f"User '{username}' not found.") from exc
            qs = qs.filter(created_by=user)
        if options.get("only_new"):
            qs = qs.filter(status=Signal.Status.NEW)
        limit = options.get("limit") or 100

        total = 0
        for signal in qs[:limit]:
            if options.get("only_missing") and hasattr(signal, "outcome") and signal.outcome.status == signal.outcome.Status.EVALUATED:
                continue
            outcome, reason = evaluate_signal_outcome(signal, lookahead_bars=options.get("lookahead_bars") or 5)
            total += 1
            self.stdout.write(f"{signal.instrument.symbol} {signal.signal_label or signal.signal_kind}: {outcome.status} / {outcome.outcome_label or '—'} ({reason})")

        self.stdout.write(self.style.SUCCESS(f"Outcome evaluation complete. Processed {total} signal(s)."))
