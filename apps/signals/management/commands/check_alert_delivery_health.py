from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.signals.services.delivery_health import get_delivery_health_summary


class Command(BaseCommand):
    help = "Check whether automatic alert delivery looks healthy enough to trust unattended runs."

    def add_arguments(self, parser):
        parser.add_argument("--no-error", action="store_true", help="Print health findings without exiting non-zero when issues are found.")

    def handle(self, *args, **options):
        no_error = bool(options.get("no_error"))
        summary = get_delivery_health_summary()
        problems: list[str] = []

        self.stdout.write(f"Delivery health window: last {summary.window_hours}h")
        self.stdout.write(f"Drought policy: {summary.drought_minutes}m")
        self.stdout.write(f"Failure streak threshold: {summary.failure_streak_threshold}")
        self.stdout.write(f"Drought status: {summary.drought_headline}")
        if summary.in_drought:
            problems.append(summary.drought_headline)

        self.stdout.write("")
        self.stdout.write("Channels:")
        for channel in summary.channels:
            last_success = channel.last_success_at.strftime("%Y-%m-%d %H:%M") if channel.last_success_at else "never"
            last_attempt = channel.last_attempt_at.strftime("%Y-%m-%d %H:%M") if channel.last_attempt_at else "never"
            line = (
                f"- {channel.channel}: enabled={'yes' if channel.enabled else 'no'} "
                f"healthy={'yes' if channel.healthy else 'no'} "
                f"sent={channel.sent_count_window} failed={channel.failed_count_window} "
                f"failure_streak={channel.failure_streak} last_attempt={last_attempt} last_success={last_success}"
            )
            self.stdout.write(line)
            self.stdout.write(f"  headline: {channel.headline}")
            if channel.enabled and not channel.healthy:
                problems.append(f"{channel.channel}: {channel.headline}")

        if problems and not no_error:
            raise CommandError("Delivery health check failed: " + " | ".join(problems))

        if problems:
            self.stdout.write(self.style.WARNING("Delivery health issues found."))
        else:
            self.stdout.write(self.style.SUCCESS("Delivery health looks healthy."))
