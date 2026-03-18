from django.core.management.base import BaseCommand

from apps.signals.services.position_monitor import monitor_open_positions


class Command(BaseCommand):
    help = "Evaluate open paper trades and emit negative position alerts when needed."

    def add_arguments(self, parser):
        parser.add_argument("--username", type=str, required=False)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        results = monitor_open_positions(username=options.get("username"), dry_run=options["dry_run"])
        if not results:
            self.stdout.write("No open paper trades to monitor.")
            return
        for item in results:
            label = item.alert_type or "—"
            self.stdout.write(f"{item.symbol} trade={item.trade_id} {label}: {item.status} ({item.message})")
