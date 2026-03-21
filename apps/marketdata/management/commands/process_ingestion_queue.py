from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.marketdata.models import IngestionJob
from apps.marketdata.services.ingestion_queue import process_next_job


class Command(BaseCommand):
    help = "Process pending market ingestion queue jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=int(getattr(settings, "INGESTION_QUEUE_MAX_JOBS_PER_CYCLE", 1) or 1),
            help="Maximum pending jobs to process in this run.",
        )

    def handle(self, *args, **options):
        max_jobs = max(1, int(options.get("max_jobs") or 1))
        processed = 0
        succeeded = 0
        failed = 0
        for _ in range(max_jobs):
            job = process_next_job()
            if not job:
                break
            processed += 1
            if job.status == IngestionJob.Status.SUCCEEDED:
                succeeded += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"job={job.id} user={job.user.username} status=SUCCEEDED asset={job.asset_class or 'ALL'} watchlist={job.watchlist_name}"
                    )
                )
            elif job.status == IngestionJob.Status.FAILED:
                failed += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"job={job.id} user={job.user.username} status=FAILED asset={job.asset_class or 'ALL'} error={job.last_error}"
                    )
                )

        pending = IngestionJob.objects.filter(status=IngestionJob.Status.PENDING).count()
        self.stdout.write(
            f"Processed ingestion queue jobs={processed} succeeded={succeeded} failed={failed} pending={pending}"
        )

