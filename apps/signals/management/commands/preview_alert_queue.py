from django.core.management.base import BaseCommand

from apps.signals.services.alerts import build_alert_queue_preview


class Command(BaseCommand):
    help = "Show the current alert queue with eligibility reasons and score gaps."

    def add_arguments(self, parser):
        parser.add_argument("--username", dest="username", default=None)
        parser.add_argument("--limit", dest="limit", type=int, default=20)

    def handle(self, *args, **options):
        username = options.get("username")
        limit = options.get("limit") or 20
        queue = build_alert_queue_preview(username=username, limit=limit)
        if not queue:
            self.stdout.write("No alert queue items found.")
            return

        self.stdout.write(f"Alert queue preview (username={username or 'all'})")
        for index, item in enumerate(queue, start=1):
            signal = item["signal"]
            explanation = item["explanation"]
            eligibility = "ELIGIBLE" if explanation.eligible else "BLOCKED"
            score = item["score_display"]
            threshold = item["threshold_display"]
            gap = item["gap_display"]
            self.stdout.write(
                f"{index:>2}. {signal.instrument.symbol} {signal.direction} {signal.timeframe} "
                f"{signal.signal_label or signal.signal_kind}: {eligibility} "
                f"reason={explanation.reason} score={score} threshold={threshold} gap={gap}"
            )
