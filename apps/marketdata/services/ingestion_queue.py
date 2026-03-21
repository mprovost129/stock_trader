from __future__ import annotations

from django.core.management import call_command
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.marketdata.models import IngestionJob


def enqueue_watchlist_ingest_job(
    *,
    user,
    watchlist_name: str = "Default",
    source: str = IngestionJob.Source.MANUAL,
    asset_class: str = "",
    stock_timeframe: str = "1d",
    crypto_timeframe: str = "1d",
    stock_provider: str = "",
    crypto_provider: str = "",
    symbols_csv: str = "",
    limit: int = 300,
    max_symbols: int = 8,
    throttle_seconds: float = 1.0,
) -> IngestionJob:
    return IngestionJob.objects.create(
        user=user,
        watchlist_name=(watchlist_name or "Default").strip() or "Default",
        source=source,
        asset_class=(asset_class or "").strip().upper(),
        stock_timeframe=(stock_timeframe or "1d").strip().lower(),
        crypto_timeframe=(crypto_timeframe or "1d").strip().lower(),
        stock_provider=(stock_provider or "").strip().lower(),
        crypto_provider=(crypto_provider or "").strip().lower(),
        symbols_csv=(symbols_csv or "").strip().upper(),
        limit=max(1, int(limit or 300)),
        max_symbols=max(1, int(max_symbols or 1)),
        throttle_seconds=max(0.0, float(throttle_seconds or 0.0)),
    )


def _claim_next_pending_job() -> IngestionJob | None:
    now = timezone.now()
    with transaction.atomic():
        job = (
            IngestionJob.objects.select_for_update()
            .filter(status=IngestionJob.Status.PENDING, run_after__lte=now)
            .order_by("run_after", "id")
            .first()
        )
        if not job:
            return None
        updated = (
            IngestionJob.objects.filter(pk=job.pk, status=IngestionJob.Status.PENDING)
            .update(
                status=IngestionJob.Status.RUNNING,
                started_at=now,
                finished_at=None,
                last_error="",
                attempt_count=F("attempt_count") + 1,
            )
        )
        if not updated:
            return None
    return IngestionJob.objects.get(pk=job.pk)


def process_next_job() -> IngestionJob | None:
    job = _claim_next_pending_job()
    if not job:
        return None

    started = timezone.now()
    try:
        call_command(
            "ingest_watchlist_prices",
            username=job.user.username,
            watchlist=job.watchlist_name,
            stock_timeframe=job.stock_timeframe,
            crypto_timeframe=job.crypto_timeframe,
            stock_provider=job.stock_provider,
            crypto_provider=job.crypto_provider,
            limit=job.limit,
            symbols=job.symbols_csv,
            max_symbols=job.max_symbols,
            throttle_seconds=job.throttle_seconds,
            asset_class=job.asset_class,
        )
        finished = timezone.now()
        job.status = IngestionJob.Status.SUCCEEDED
        job.finished_at = finished
        job.result_summary = {
            "duration_seconds": round((finished - started).total_seconds(), 2),
            "watchlist": job.watchlist_name,
            "asset_class": job.asset_class or "ALL",
            "max_symbols": job.max_symbols,
            "throttle_seconds": job.throttle_seconds,
        }
        job.last_error = ""
        job.save(update_fields=["status", "finished_at", "result_summary", "last_error", "updated_at"])
    except Exception as exc:  # noqa: BLE001
        finished = timezone.now()
        job.status = IngestionJob.Status.FAILED
        job.finished_at = finished
        job.last_error = str(exc)
        job.result_summary = {
            "duration_seconds": round((finished - started).total_seconds(), 2),
            "error_type": exc.__class__.__name__,
        }
        job.save(update_fields=["status", "finished_at", "last_error", "result_summary", "updated_at"])
    return job

