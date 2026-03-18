from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.signals.services.escalation import check_and_send_portfolio_health_notification


class Command(BaseCommand):
    help = "Check portfolio health snapshots for deterioration and notify the operator if the score has dropped."

    def add_arguments(self, parser):
        parser.add_argument("--username", help="Check a single user.")
        parser.add_argument("--all-users", action="store_true", help="Check every user.")
        parser.add_argument("--dry-run", action="store_true", help="Record dry-run notifications without sending them.")

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        all_users = bool(options.get("all_users"))
        dry_run = bool(options.get("dry_run"))

        if not username and not all_users:
            raise CommandError("Provide --username or --all-users.")

        User = get_user_model()
        qs = User.objects.all().order_by("id") if all_users else User.objects.filter(username=username)
        if username and not qs.exists():
            raise CommandError(f"Unknown user: {username}")

        for user in qs:
            result = check_and_send_portfolio_health_notification(user=user, dry_run=dry_run)
            self.stdout.write(result.headline)
            if not result.triggered:
                self.stdout.write(f"  Status: {result.reason}")
                continue
            for r in result.results:
                self.stdout.write(f"  [{r.channel}] {r.status} ({r.reason})")
