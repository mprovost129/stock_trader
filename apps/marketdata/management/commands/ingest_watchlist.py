from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.marketdata.services.watchlist_ingestion import build_watchlist_ingest_plan, execute_watchlist_ingest_plan
from apps.marketdata.models import Instrument
from apps.portfolios.models import Watchlist


class Command(BaseCommand):
    help = "Ingest bars for a user's watchlist with backlog prioritization (missing bars first, stalest next)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--watchlist", default="Default")
        parser.add_argument("--stock-timeframe", default="1d")
        parser.add_argument("--crypto-timeframe", default="1d")
        parser.add_argument("--stock-provider", default=None)
        parser.add_argument("--crypto-provider", default="", help="Optional crypto provider override: coinbase|kraken|binance. Blank = auto-route.")
        parser.add_argument("--limit", type=int, default=300)
        parser.add_argument("--max-symbols", type=int, default=0, help="Maximum watchlist symbols to ingest this run. 0 = all matched symbols.")
        parser.add_argument("--throttle-seconds", type=float, default=0.0, help="Optional pause between provider calls for rate-limited plans.")
        parser.add_argument("--asset-class", default="", help="Optional filter: STOCK or CRYPTO")
        parser.add_argument(
            "--symbols",
            default="",
            help="Optional comma-separated symbol filter. Only these symbols will be ingested.",
        )

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        watchlist_name = (options.get("watchlist") or "Default").strip() or "Default"
        stock_timeframe = (options.get("stock_timeframe") or "1d").strip().lower()
        crypto_timeframe = (options.get("crypto_timeframe") or "1d").strip().lower()
        stock_provider = (options.get("stock_provider") or "").strip().lower() or None
        crypto_provider = (options.get("crypto_provider") or "").strip().lower() or None
        symbol_filter = [item.strip().upper() for item in (options.get("symbols") or "").split(",") if item.strip()]
        limit = int(options.get("limit") or 300)
        max_symbols = int(options.get("max_symbols") or 0)
        throttle_seconds = float(options.get("throttle_seconds") or 0.0)
        asset_class = (options.get("asset_class") or "").strip().upper() or None
        if asset_class and asset_class not in {Instrument.AssetClass.STOCK, Instrument.AssetClass.CRYPTO}:
            raise CommandError("--asset-class must be STOCK or CRYPTO")

        User = get_user_model()
        if not User.objects.filter(username=username).exists():
            raise CommandError(f"User not found: {username}")

        wl = Watchlist.objects.filter(user__username=username, name=watchlist_name).first()
        if not wl:
            raise CommandError(f"Watchlist not found for {username}: {watchlist_name}")

        plan, selected_count = build_watchlist_ingest_plan(
            watchlist=wl,
            stock_timeframe=stock_timeframe,
            crypto_timeframe=crypto_timeframe,
            stock_provider=stock_provider,
            crypto_provider=crypto_provider,
            symbols=symbol_filter,
            asset_class=asset_class,
            max_symbols=max_symbols or None,
        )
        if not plan:
            self.stdout.write(self.style.WARNING("No watchlist instruments matched for ingestion."))
            return

        missing_before = sum(1 for item in plan if not item.has_bars)
        refresh_before = len(plan) - missing_before
        throttle_msg = f" throttle={throttle_seconds}s" if throttle_seconds else ""
        self.stdout.write(
            f"Watchlist ingest plan for {username}/{wl.name}: selected={selected_count} scheduled={len(plan)} missing_first={missing_before} refresh_existing={refresh_before}{throttle_msg}"
        )

        def _on_success(idx, item, result):
            mode = "BACKFILL" if not item.has_bars else "REFRESH"
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{idx}/{len(plan)}] {mode} {item.instrument.symbol} {item.timeframe}: created={result.created} updated={result.updated}"
                )
            )

        def _on_failure(idx, item, exc):
            mode = "BACKFILL" if not item.has_bars else "REFRESH"
            self.stdout.write(self.style.WARNING(f"[{idx}/{len(plan)}] {mode} {item.instrument.symbol} {item.timeframe}: failed ({exc})"))

        summary = execute_watchlist_ingest_plan(
            watchlist=wl,
            items=plan,
            throttle_seconds=throttle_seconds,
            limit=limit,
            on_success=_on_success,
            on_failure=_on_failure,
        )

        msg = (
            f"Watchlist ingest complete. scheduled={summary.planned_count} success={summary.success_count} failed={summary.failure_count} "
            f"backfilled={summary.missing_count} refreshed={summary.refreshed_count} created={summary.created_total} updated={summary.updated_total}"
        )
        if summary.failure_count:
            self.stdout.write(self.style.WARNING(msg))
        else:
            self.stdout.write(self.style.SUCCESS(msg))
