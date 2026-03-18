from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.portfolios.services import save_portfolio_health_snapshot


class Command(BaseCommand):
    help = "Persist a portfolio health snapshot for one user or all users."

    def add_arguments(self, parser):
        parser.add_argument("--username", help="Save a snapshot for a single user.")
        parser.add_argument("--all-users", action="store_true", help="Save snapshots for every user.")

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        all_users = bool(options.get("all_users"))
        if not username and not all_users:
            raise CommandError("Provide --username or --all-users.")

        User = get_user_model()
        qs = User.objects.all().order_by("id") if all_users else User.objects.filter(username=username)
        if username and not qs.exists():
            raise CommandError(f"Unknown user: {username}")

        count = 0
        for user in qs:
            snapshot = save_portfolio_health_snapshot(user=user)
            count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"{user.username}: saved portfolio health snapshot score={snapshot.overall_score} grade={snapshot.overall_grade_label or snapshot.overall_grade_code}"
                )
            )
        self.stdout.write(self.style.SUCCESS(f"Completed {count} snapshot(s)."))
