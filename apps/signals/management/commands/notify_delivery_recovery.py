from django.core.management.base import BaseCommand

from apps.signals.services.escalation import check_and_send_delivery_recovery_notification


class Command(BaseCommand):
    help = "Send an operator recovery notice when delivery health returns to normal after an escalation incident."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        result = check_and_send_delivery_recovery_notification(dry_run=dry_run)
        self.stdout.write(f"triggered={result.triggered} reason={result.reason} headline={result.headline}")
        for item in result.results:
            self.stdout.write(f"- {item.channel}: {item.status} ({item.reason})")
