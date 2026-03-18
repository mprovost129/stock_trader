from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.signals.services.alerts import (
    DiscordWebhookError,
    EmailAlertError,
    get_enabled_delivery_channels,
    send_test_discord_message,
    send_test_email_message,
)


class Command(BaseCommand):
    help = "Send a manual test alert across enabled delivery channels."

    def add_arguments(self, parser):
        parser.add_argument("--title", default="Trading Advisor test alert", help="Optional title.")
        parser.add_argument("--body", default="Alert delivery wiring is working.", help="Optional body.")
        parser.add_argument("--dry-run", action="store_true", help="Print payloads without posting or emailing.")

    def handle(self, *args, **options):
        title = (options.get("title") or "Trading Advisor test alert").strip()
        body = (options.get("body") or "Alert delivery wiring is working.").strip()
        dry_run = bool(options.get("dry_run"))

        channels = get_enabled_delivery_channels()
        if not channels:
            raise CommandError("No delivery channels are enabled. Configure ALERT_DELIVERY_* settings first.")

        for channel in channels:
            if channel == "DISCORD":
                try:
                    result = send_test_discord_message(title=title, body=body, dry_run=dry_run)
                except DiscordWebhookError as exc:
                    raise CommandError(str(exc)) from exc
            elif channel == "EMAIL":
                try:
                    result = send_test_email_message(subject=title, body=body, dry_run=dry_run)
                except EmailAlertError as exc:
                    raise CommandError(str(exc)) from exc
            else:
                continue
            if dry_run:
                self.stdout.write(self.style.WARNING(f"[{channel}] dry run only"))
                self.stdout.write(str(result["payload"]))
            else:
                self.stdout.write(self.style.SUCCESS(f"[{channel}] test alert sent successfully."))
