from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.portfolios.services import check_open_held_positions


class Command(BaseCommand):
    help = "Evaluate manually entered held positions and send alerts when they go bad."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        user = None
        if username:
            user = get_user_model().objects.filter(username=username).first()
            if user is None:
                raise CommandError(f"Unknown user: {username}")
        alerts = check_open_held_positions(user=user, dry_run=bool(options.get("dry_run")))
        sent = sum(1 for item in alerts if item.status == item.Status.SENT)
        self.stdout.write(self.style.SUCCESS(f"Held position check complete. alerts={len(alerts)} sent={sent}"))
