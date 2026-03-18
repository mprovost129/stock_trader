from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.signals.services.escalation import check_and_send_delivery_health_escalation


class Command(BaseCommand):
    help = "Escalate delivery-health failures through the enabled operator channels."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Record dry-run notifications without sending them.")

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        summary = check_and_send_delivery_health_escalation(dry_run=dry_run)
        self.stdout.write(summary.headline)
        if not summary.triggered:
            self.stdout.write(f"Status: {summary.reason}")
            return
        for result in summary.results:
            self.stdout.write(f"[{result.channel}] {result.status} ({result.reason})")
