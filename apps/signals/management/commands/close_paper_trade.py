from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from apps.signals.models import PaperTrade
from apps.signals.services.paper_trading import close_paper_trade


class Command(BaseCommand):
    help = "Close an open paper trade."

    def add_arguments(self, parser):
        parser.add_argument("--trade-id", type=int, required=True)
        parser.add_argument("--exit-price", type=str, required=False)
        parser.add_argument("--notes", type=str, default="")

    def handle(self, *args, **options):
        try:
            trade = PaperTrade.objects.select_related("signal").get(pk=options["trade_id"])
        except PaperTrade.DoesNotExist as exc:
            raise CommandError("Paper trade not found.") from exc

        exit_price = None
        raw = options.get("exit_price")
        if raw:
            try:
                exit_price = Decimal(raw)
            except InvalidOperation as exc:
                raise CommandError("Invalid exit price.") from exc

        result = close_paper_trade(trade=trade, exit_price=exit_price, notes=options["notes"])
        self.stdout.write(self.style.SUCCESS(f"Paper trade closed: id={trade.id} pnl={result.realized_pnl_amount} pnl_pct={result.realized_pnl_pct}"))
