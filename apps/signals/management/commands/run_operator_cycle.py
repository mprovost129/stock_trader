from __future__ import annotations

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Run the operator workflow end-to-end: health checks, ingest, scan, alerts, monitoring, outcomes, and previews."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--watchlist", default="Default")
        parser.add_argument("--limit", type=int, default=300)
        parser.add_argument("--stock-timeframe", default=getattr(settings, "SCHEDULER_STOCK_TIMEFRAME", "1d"))
        parser.add_argument("--crypto-timeframe", default=getattr(settings, "SCHEDULER_CRYPTO_TIMEFRAME", "1d"))
        parser.add_argument("--stock-provider", default=getattr(settings, "SCHEDULER_STOCK_PROVIDER", ""))
        parser.add_argument("--crypto-provider", default=getattr(settings, "SCHEDULER_CRYPTO_PROVIDER", "coinbase"))
        parser.add_argument("--symbols", default="")
        parser.add_argument("--max-symbols", type=int, default=int(getattr(settings, "SCHEDULER_MAX_SYMBOLS_PER_CYCLE", 25) or 25))
        parser.add_argument("--throttle-seconds", type=float, default=float(getattr(settings, "SCHEDULER_THROTTLE_SECONDS", 0) or 0))
        parser.add_argument("--lookahead-bars", type=int, default=5)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--verbose-scan", action="store_true")
        parser.add_argument("--skip-health-check", action="store_true")
        parser.add_argument("--skip-outcomes", action="store_true")
        parser.add_argument("--skip-analytics", action="store_true")

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        if not username:
            raise CommandError("--username is required")

        watchlist = options.get("watchlist") or "Default"
        limit = int(options.get("limit") or 300)
        stock_timeframe = (options.get("stock_timeframe") or "1d").strip().lower()
        crypto_timeframe = (options.get("crypto_timeframe") or "1d").strip().lower()
        stock_provider = (options.get("stock_provider") or "").strip().lower()
        crypto_provider = (options.get("crypto_provider") or "coinbase").strip().lower()
        symbols = (options.get("symbols") or "").strip()
        max_symbols = int(options.get("max_symbols") or 0)
        throttle_seconds = float(options.get("throttle_seconds") or 0)
        dry_run = bool(options.get("dry_run"))

        _assert_schema_ready()

        self.stdout.write(self.style.SUCCESS("Starting operator cycle."))
        self.stdout.write(
            f"username={username} watchlist={watchlist} dry_run={dry_run} max_symbols={max_symbols or 'all'} throttle={throttle_seconds}s"
        )

        if not options.get("skip_health_check"):
            providers = [crypto_provider or "coinbase"]
            stock_default = stock_provider or getattr(settings, "STOCK_DAILY_PROVIDER", "polygon")
            if stock_default:
                providers.append(stock_default)
            ordered = []
            for provider in providers:
                if provider and provider not in ordered:
                    ordered.append(provider)
            self.stdout.write("Running provider health checks...")
            call_command("provider_healthcheck", providers=",".join(ordered))

        self.stdout.write("Running watchlist ingestion...")
        call_command(
            "ingest_watchlist_prices",
            username=username,
            watchlist=watchlist,
            stock_timeframe=stock_timeframe,
            crypto_timeframe=crypto_timeframe,
            stock_provider=stock_provider,
            crypto_provider=crypto_provider,
            limit=limit,
            symbols=symbols,
            max_symbols=max_symbols,
            throttle_seconds=throttle_seconds,
        )

        self.stdout.write("Running scans...")
        call_command("run_scans", username=username, watchlist=watchlist, limit=limit, verbose=bool(options.get("verbose_scan", False)))

        self.stdout.write("Evaluating alert queue...")
        call_command("send_alerts", username=username, dry_run=dry_run)

        self.stdout.write("Monitoring positions...")
        call_command("monitor_positions", username=username, dry_run=dry_run)

        if not options.get("skip_outcomes"):
            self.stdout.write("Evaluating signal outcomes...")
            call_command(
                "evaluate_signal_outcomes",
                username=username,
                lookahead_bars=int(options.get("lookahead_bars") or 5),
                only_missing=True,
                limit=limit,
            )

        self.stdout.write("Previewing alert queue...")
        call_command("preview_alert_queue", username=username, limit=10)
        self.stdout.write("Previewing next stock session queue...")
        call_command("preview_next_session_queue", username=username, limit=10)

        if not options.get("skip_analytics"):
            self.stdout.write("Analyzing trade performance...")
            call_command("analyze_trade_performance", username=username)

        self.stdout.write(self.style.SUCCESS("Operator cycle complete."))


def _assert_schema_ready() -> None:
    connection = connections[DEFAULT_DB_ALIAS]
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    if executor.migration_plan(targets):
        raise CommandError("Database schema is not up to date. Run: python manage.py migrate")
