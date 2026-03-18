from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.marketdata.models import Instrument, PriceBar
from apps.portfolios.models import Watchlist, InstrumentSelection


class Command(BaseCommand):
    help = "Backfill market data for a user's watchlist in controlled batches until backlog is reduced or max cycles is reached."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--watchlist", default="Default")
        parser.add_argument("--asset-class", default="", help="Optional filter: STOCK or CRYPTO")
        parser.add_argument("--timeframe", default="1d")
        parser.add_argument("--batch-size", type=int, default=25)
        parser.add_argument("--limit", type=int, default=300)
        parser.add_argument("--max-cycles", type=int, default=10)
        parser.add_argument("--throttle-seconds", type=float, default=0.0)
        parser.add_argument("--stock-provider", default="")
        parser.add_argument("--crypto-provider", default="", help="Optional crypto provider override. Blank = auto-route with unsupported-pair and cooldown skipping.")
        parser.add_argument("--stop-when-complete", action="store_true")

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        watchlist_name = (options.get("watchlist") or "Default").strip() or "Default"
        asset_class = (options.get("asset_class") or "").strip().upper() or None
        timeframe = (options.get("timeframe") or "1d").strip().lower()
        batch_size = int(options.get("batch_size") or 25)
        limit = int(options.get("limit") or 300)
        max_cycles = int(options.get("max_cycles") or 10)
        throttle_seconds = float(options.get("throttle_seconds") or 0.0)
        stock_provider = (options.get("stock_provider") or "").strip().lower()
        crypto_provider = (options.get("crypto_provider") or "").strip().lower()
        stop_when_complete = bool(options.get("stop_when_complete"))

        if asset_class and asset_class not in {Instrument.AssetClass.STOCK, Instrument.AssetClass.CRYPTO}:
            raise CommandError("--asset-class must be STOCK or CRYPTO")

        wl = Watchlist.objects.filter(user__username=username, name=watchlist_name).first()
        if not wl:
            raise CommandError(f"Watchlist not found for {username}: {watchlist_name}")

        self.stdout.write(self.style.SUCCESS(
            f"Starting market-data backfill for {username}/{watchlist_name} timeframe={timeframe} batch_size={batch_size} max_cycles={max_cycles}"
        ))

        previous_missing = None
        for cycle in range(1, max_cycles + 1):
            selected_count, ready_count = _coverage_snapshot(watchlist=wl, timeframe=timeframe, asset_class=asset_class)
            missing_count = max(selected_count - ready_count, 0)
            self.stdout.write(
                f"Cycle {cycle}: selected={selected_count} data_ready={ready_count} missing={missing_count}"
            )

            if stop_when_complete and missing_count == 0:
                self.stdout.write(self.style.SUCCESS("Backfill complete: no missing symbols remain for the selected scope."))
                break

            if previous_missing is not None and missing_count >= previous_missing and cycle > 1:
                self.stdout.write(self.style.WARNING("No coverage improvement detected since the last cycle. Stopping early."))
                break
            previous_missing = missing_count

            call_command(
                "ingest_watchlist_prices",
                username=username,
                watchlist=watchlist_name,
                asset_class=asset_class or "",
                stock_timeframe=timeframe,
                crypto_timeframe=timeframe,
                stock_provider=stock_provider,
                crypto_provider=crypto_provider,
                limit=limit,
                max_symbols=batch_size,
                throttle_seconds=throttle_seconds,
            )

        selected_count, ready_count = _coverage_snapshot(watchlist=wl, timeframe=timeframe, asset_class=asset_class)
        self.stdout.write(self.style.SUCCESS(
            f"Backfill finished: selected={selected_count} data_ready={ready_count} missing={max(selected_count - ready_count, 0)}"
        ))


def _coverage_snapshot(*, watchlist: Watchlist, timeframe: str, asset_class: str | None) -> tuple[int, int]:
    selections_qs = InstrumentSelection.objects.filter(watchlist=watchlist, is_active=True, instrument__is_active=True)
    if asset_class:
        selections_qs = selections_qs.filter(instrument__asset_class=asset_class)
    instrument_ids = list(selections_qs.values_list("instrument_id", flat=True))
    if not instrument_ids:
        return 0, 0
    ready_count = (
        PriceBar.objects.filter(instrument_id__in=instrument_ids, timeframe=timeframe)
        .values("instrument_id")
        .annotate(c=Count("id"))
        .count()
    )
    return len(instrument_ids), int(ready_count)
