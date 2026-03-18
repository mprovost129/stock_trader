from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run scans, then evaluate/send alerts in one disciplined cycle."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Username to scan and alert for.")
        parser.add_argument("--watchlist", default="Default")
        parser.add_argument("--limit", type=int, default=300)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        username = options["username"]
        watchlist = options["watchlist"]
        limit = options["limit"]
        dry_run = bool(options.get("dry_run"))

        call_command("run_scans", username=username, watchlist=watchlist, limit=limit)
        call_command("send_alerts", username=username, dry_run=dry_run)
