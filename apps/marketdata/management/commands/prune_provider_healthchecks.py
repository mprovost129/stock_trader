from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.marketdata.models import ProviderHealthCheck


class Command(BaseCommand):
    help = "Prune old provider health-check history so reliability tracking stays operationally useful without growing forever."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=int(getattr(settings, "PROVIDER_HEALTH_RETENTION_DAYS", 30) or 30),
            help="Keep records newer than this many days. Defaults to PROVIDER_HEALTH_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many records would be deleted without deleting them.",
        )

    def handle(self, *args, **options):
        days = max(int(options.get("days") or 30), 1)
        dry_run = bool(options.get("dry_run"))
        cutoff = timezone.now() - timedelta(days=days)

        qs = ProviderHealthCheck.objects.filter(checked_at__lt=cutoff)
        delete_count = qs.count()
        total_before = ProviderHealthCheck.objects.count()

        self.stdout.write(
            f"Provider health retention window: keep last {days} day(s). Cutoff: {timezone.localtime(cutoff).strftime('%Y-%m-%d %I:%M %p %Z')}"
        )
        self.stdout.write(f"Records before prune: {total_before}")
        self.stdout.write(f"Records older than cutoff: {delete_count}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run only. No provider health records were deleted."))
            return

        deleted, _details = qs.delete()
        remaining = ProviderHealthCheck.objects.count()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} old provider health record(s). Remaining: {remaining}"))
