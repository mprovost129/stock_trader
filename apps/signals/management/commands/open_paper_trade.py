from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.signals.models import Signal
from apps.signals.services.paper_trading import open_paper_trade_from_signal


class Command(BaseCommand):
    help = "Open a paper trade from a signal."

    def add_arguments(self, parser):
        parser.add_argument("--signal-id", type=int, required=True)
        parser.add_argument("--username", type=str, required=False)
        parser.add_argument("--notes", type=str, default="")

    def handle(self, *args, **options):
        try:
            signal = Signal.objects.select_related("trade_plan").get(pk=options["signal_id"])
        except Signal.DoesNotExist as exc:
            raise CommandError("Signal not found.") from exc

        user = None
        username = options.get("username")
        if username:
            User = get_user_model()
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist as exc:
                raise CommandError(f"User '{username}' not found.") from exc

        result = open_paper_trade_from_signal(signal=signal, user=user, notes=options["notes"])
        state = "created" if result.created else "existing"
        self.stdout.write(self.style.SUCCESS(f"Paper trade {state}: id={result.trade.id} symbol={result.trade.signal.instrument.symbol} entry={result.trade.entry_price}"))
