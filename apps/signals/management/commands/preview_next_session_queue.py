from django.core.management.base import BaseCommand

from apps.signals.services.alerts import build_next_session_queue


class Command(BaseCommand):
    help = "Show stock signals that are otherwise viable but are currently blocked only by a closed market session."

    def add_arguments(self, parser):
        parser.add_argument("--username", dest="username", default=None)
        parser.add_argument("--limit", dest="limit", type=int, default=10)

    def handle(self, *args, **options):
        username = options.get("username")
        limit = options.get("limit") or 10
        queue = build_next_session_queue(username=username, limit=limit)
        if not queue:
            self.stdout.write("No next-session stock queue items found.")
            return

        self.stdout.write(f"Next stock session queue (username={username or 'all'})")
        for index, item in enumerate(queue, start=1):
            signal = item["signal"]
            explanation = item["explanation"]
            self.stdout.write(
                f"{index:>2}. {signal.instrument.symbol} {signal.direction} {signal.timeframe} "
                f"{signal.signal_label or signal.signal_kind}: blocked_by_session "
                f"score={item['score_display']} threshold={item['threshold_display']} gap={item['gap_display']}"
            )
