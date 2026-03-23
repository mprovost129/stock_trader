from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.signals.services.escalation import check_and_send_daily_alert_digest


class Command(BaseCommand):
    help = "Send an end-of-day alert-delivery digest after market close."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="", help="Optional username scope for digest stats.")
        parser.add_argument("--dry-run", action="store_true", help="Record dry-run notifications without sending.")

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip() or None
        dry_run = bool(options.get("dry_run"))

        summary = check_and_send_daily_alert_digest(username=username, dry_run=dry_run)
        self.stdout.write(summary.headline)
        self.stdout.write(f"Status: {summary.reason}")
        for item in summary.results:
            self.stdout.write(f"[{item.channel}] {item.status} ({item.reason})")
