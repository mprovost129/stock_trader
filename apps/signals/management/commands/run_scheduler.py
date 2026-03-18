from __future__ import annotations

import time
from dataclasses import dataclass

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.marketdata.services.runtime import classify_runtime_mode


@dataclass(frozen=True)
class CycleSummary:
    iteration: int
    started_at: str
    finished_at: str
    duration_seconds: float
    status: str
    message: str


class Command(BaseCommand):
    help = "Run disciplined operator cycles on an interval with optional market-aware pacing."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--watchlist", default="Default")
        parser.add_argument("--iterations", type=int, default=1, help="0 = run forever")
        parser.add_argument("--sleep-seconds", type=int, default=int(getattr(settings, "SCHEDULER_INTERVAL_SECONDS", 300) or 300))
        parser.add_argument("--limit", type=int, default=300)
        parser.add_argument("--stock-timeframe", default=getattr(settings, "SCHEDULER_STOCK_TIMEFRAME", "1d"))
        parser.add_argument("--crypto-timeframe", default=getattr(settings, "SCHEDULER_CRYPTO_TIMEFRAME", "1d"))
        parser.add_argument("--stock-provider", default=getattr(settings, "SCHEDULER_STOCK_PROVIDER", ""))
        parser.add_argument("--crypto-provider", default=getattr(settings, "SCHEDULER_CRYPTO_PROVIDER", "coinbase"))
        parser.add_argument("--symbols", default="")
        parser.add_argument("--max-symbols", type=int, default=int(getattr(settings, "SCHEDULER_MAX_SYMBOLS_PER_CYCLE", 25) or 25))
        parser.add_argument("--throttle-seconds", type=float, default=float(getattr(settings, "SCHEDULER_THROTTLE_SECONDS", 0) or 0))
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-health-check", action="store_true")
        parser.add_argument("--verbose-scan", action="store_true", help="Pass verbose diagnostics through to run_scans.")
        parser.add_argument("--market-aware", action="store_true", default=bool(getattr(settings, "SCHEDULER_MARKET_AWARE", True)), help="Use market-open vs market-closed pacing.")
        parser.add_argument("--open-sleep-seconds", type=int, default=int(getattr(settings, "SCHEDULER_OPEN_SLEEP_SECONDS", 300) or 300))
        parser.add_argument("--closed-sleep-seconds", type=int, default=int(getattr(settings, "SCHEDULER_CLOSED_SLEEP_SECONDS", 3600) or 3600))
        parser.add_argument("--healthcheck-every", type=int, default=int(getattr(settings, "SCHEDULER_HEALTHCHECK_EVERY", 12) or 12), help="Run provider health checks every N cycles (1 = every cycle).")
        parser.add_argument("--delivery-escalation-every", type=int, default=int(getattr(settings, "SCHEDULER_DELIVERY_ESCALATION_EVERY", 1) or 1), help="Run delivery-health escalation checks every N cycles (1 = every cycle).")
        parser.add_argument("--delivery-recovery-every", type=int, default=int(getattr(settings, "SCHEDULER_DELIVERY_RECOVERY_EVERY", 1) or 1), help="Run delivery-health recovery checks every N cycles (1 = every cycle).")
        parser.add_argument("--position-sync-every", type=int, default=int(getattr(settings, "SCHEDULER_POSITION_SYNC_EVERY", 1) or 1), help="Run paper-trade lifecycle sync every N cycles (1 = every cycle).")
        parser.add_argument("--held-position-check-every", type=int, default=int(getattr(settings, "SCHEDULER_HELD_POSITION_CHECK_EVERY", 1) or 1), help="Run held-position health checks every N cycles (1 = every cycle).")
        parser.add_argument("--portfolio-snapshot-every", type=int, default=int(getattr(settings, "SCHEDULER_PORTFOLIO_SNAPSHOT_EVERY", 4) or 4), help="Save a portfolio health snapshot and check for deterioration every N cycles (0 = disabled).")

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        if not username:
            raise CommandError("--username is required")

        iterations = int(options.get("iterations") or 1)
        sleep_seconds = max(1, int(options.get("sleep_seconds") or 300))
        watchlist = options.get("watchlist") or "Default"
        limit = int(options.get("limit") or 300)
        stock_timeframe = (options.get("stock_timeframe") or "1d").strip().lower()
        crypto_timeframe = (options.get("crypto_timeframe") or "1d").strip().lower()
        stock_provider = (options.get("stock_provider") or "").strip().lower() or None
        crypto_provider = (options.get("crypto_provider") or "coinbase").strip().lower() or None
        symbols = (options.get("symbols") or "").strip()
        max_symbols = int(options.get("max_symbols") or 0)
        throttle_seconds = float(options.get("throttle_seconds") or 0)
        dry_run = bool(options.get("dry_run"))
        skip_health_check = bool(options.get("skip_health_check"))
        market_aware = bool(options.get("market_aware"))
        open_sleep_seconds = max(1, int(options.get("open_sleep_seconds") or sleep_seconds))
        closed_sleep_seconds = max(1, int(options.get("closed_sleep_seconds") or max(sleep_seconds, 3600)))
        healthcheck_every = max(1, int(options.get("healthcheck_every") or 1))
        delivery_escalation_every = max(1, int(options.get("delivery_escalation_every") or 1))
        delivery_recovery_every = max(1, int(options.get("delivery_recovery_every") or 1))
        position_sync_every = max(1, int(options.get("position_sync_every") or 1))
        held_position_check_every = max(1, int(options.get("held_position_check_every") or 1))
        portfolio_snapshot_every = max(0, int(options.get("portfolio_snapshot_every") or 0))

        self.stdout.write(self.style.SUCCESS("Starting scheduled runner."))
        self.stdout.write(
            f"username={username} watchlist={watchlist} iterations={iterations} dry_run={dry_run} "
            f"max_symbols={max_symbols or 'all'} throttle={throttle_seconds}s market_aware={market_aware}"
        )

        def _run_health_check() -> None:
            if skip_health_check:
                return
            self.stdout.write("Running provider health checks...")
            providers = [crypto_provider or "coinbase"]
            providers.append(stock_provider or getattr(settings, "STOCK_DAILY_PROVIDER", "polygon"))
            seen: list[str] = []
            ordered = [p for p in providers if p and not (p in seen or seen.append(p))]
            call_command("provider_healthcheck", providers=",".join(ordered))

        _run_health_check()


        def _run_delivery_escalation_check() -> None:
            self.stdout.write("Running delivery-health escalation check...")
            call_command("escalate_delivery_health", dry_run=dry_run)

        def _run_delivery_recovery_check() -> None:
            self.stdout.write("Running delivery-health recovery check...")
            call_command("notify_delivery_recovery", dry_run=dry_run)

        def _run_position_sync() -> None:
            self.stdout.write("Running paper-trade lifecycle sync...")
            call_command("sync_trade_lifecycle")

        def _run_held_position_check() -> None:
            self.stdout.write("Running held-position health check...")
            call_command("check_held_positions", username=username, dry_run=dry_run)

        def _run_portfolio_snapshot() -> None:
            self.stdout.write("Saving portfolio health snapshot...")
            call_command("save_portfolio_health_snapshot", username=username)
            self.stdout.write("Checking portfolio health for deterioration...")
            call_command("check_portfolio_health_deterioration", username=username, dry_run=dry_run)

        iteration = 0
        while True:
            iteration += 1
            cycle_started = timezone.now()
            status = "ok"
            message = "cycle_complete"
            runtime = classify_runtime_mode(
                start=getattr(settings, "EQUITY_ALERT_SESSION_START", "09:30"),
                end=getattr(settings, "EQUITY_ALERT_SESSION_END", "16:00"),
            )
            try:
                self.stdout.write("")
                self.stdout.write(
                    f"=== Cycle {iteration} started {timezone.localtime(cycle_started).strftime('%Y-%m-%d %I:%M:%S %p %Z')} "
                    f"mode={runtime.name} reason={runtime.reason} ==="
                )
                if iteration > 1 and (iteration - 1) % healthcheck_every == 0:
                    _run_health_check()

                call_command(
                    "run_operator_cycle",
                    username=username,
                    watchlist=watchlist,
                    limit=limit,
                    stock_timeframe=stock_timeframe,
                    crypto_timeframe=crypto_timeframe,
                    stock_provider=stock_provider or "",
                    crypto_provider=crypto_provider or "",
                    symbols=symbols,
                    max_symbols=max_symbols,
                    throttle_seconds=throttle_seconds,
                    dry_run=dry_run,
                    verbose_scan=bool(options.get("verbose_scan", False)),
                    skip_health_check=True,
                )
                if iteration % delivery_escalation_every == 0:
                    _run_delivery_escalation_check()
                if iteration % delivery_recovery_every == 0:
                    _run_delivery_recovery_check()
                if iteration % position_sync_every == 0:
                    _run_position_sync()
                if iteration % held_position_check_every == 0:
                    _run_held_position_check()
                if portfolio_snapshot_every > 0 and iteration % portfolio_snapshot_every == 0:
                    _run_portfolio_snapshot()
            except Exception as exc:  # noqa: BLE001
                status = "error"
                message = str(exc)
                self.stdout.write(self.style.ERROR(f"Cycle {iteration} failed: {exc}"))

            cycle_finished = timezone.now()
            summary = CycleSummary(
                iteration=iteration,
                started_at=timezone.localtime(cycle_started).strftime("%Y-%m-%d %I:%M:%S %p %Z"),
                finished_at=timezone.localtime(cycle_finished).strftime("%Y-%m-%d %I:%M:%S %p %Z"),
                duration_seconds=round((cycle_finished - cycle_started).total_seconds(), 2),
                status=status,
                message=message,
            )
            self.stdout.write(
                f"Cycle {summary.iteration} finished {summary.finished_at} status={summary.status} duration={summary.duration_seconds}s message={summary.message}"
            )

            if iterations and iteration >= iterations:
                self.stdout.write(self.style.SUCCESS("Scheduled runner complete."))
                break

            next_sleep = sleep_seconds
            if market_aware:
                runtime = classify_runtime_mode(
                    start=getattr(settings, "EQUITY_ALERT_SESSION_START", "09:30"),
                    end=getattr(settings, "EQUITY_ALERT_SESSION_END", "16:00"),
                )
                next_sleep = open_sleep_seconds if runtime.market_open else closed_sleep_seconds
            self.stdout.write(f"Sleeping {next_sleep} second(s) before next cycle...")
            time.sleep(next_sleep)
