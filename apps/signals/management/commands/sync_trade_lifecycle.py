from django.core.management.base import BaseCommand

from apps.signals.services.lifecycle import sync_open_trade_lifecycles


class Command(BaseCommand):
    help = "Sync open paper-trade lifecycle state from the latest price bars."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Optional cap on the number of open trades to sync.")

    def handle(self, *args, **options):
        limit = int(options.get("limit") or 0) or None
        results = sync_open_trade_lifecycles(limit=limit)
        changed = 0
        for result in results:
            if result.changed:
                changed += 1
            self.stdout.write(f"{result.trade.signal.instrument.symbol}: changed={result.changed} headline={result.headline}")
        self.stdout.write(self.style.SUCCESS(f"Lifecycle sync complete. synced={len(results)} changed={changed}"))
