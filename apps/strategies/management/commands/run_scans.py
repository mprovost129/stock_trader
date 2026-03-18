from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.marketdata.models import Instrument
from apps.portfolios.models import Watchlist, InstrumentSelection
from apps.strategies.models import StrategyRunConfig
from apps.strategies.services.runner import RunConfigResult, run_config


class Command(BaseCommand):
    help = "Run active strategy configs against a user's active watchlist instruments and generate NEW signals + trade plans."

    def add_arguments(self, parser):
        parser.add_argument("--username", help="Run scans for a specific username (default: all users).")
        parser.add_argument("--watchlist", default="Default", help="Watchlist name (default: Default).")
        parser.add_argument("--limit", type=int, default=300, help="Number of bars to load per instrument.")
        parser.add_argument("--verbose", action="store_true", help="Print per-symbol diagnostics when no signal fires.")

    def handle(self, *args, **options):
        username = options.get("username")
        watchlist_name = options.get("watchlist") or "Default"
        limit = options.get("limit") or 300
        verbose = bool(options.get("verbose"))

        User = get_user_model()
        if username:
            users = list(User.objects.filter(username=username))
            if not users:
                raise CommandError(f"User not found: {username}")
        else:
            users = list(User.objects.all())

        configs = list(
            StrategyRunConfig.objects.select_related("strategy")
            .filter(is_active=True, strategy__is_enabled=True)
            .order_by("strategy__slug", "timeframe")
        )
        if not configs:
            self.stdout.write(self.style.WARNING("No active StrategyRunConfig rows found. Configure in admin or run: python manage.py ensure_default_setup --username <user>"))
            return

        total_created = 0
        for user in users:
            wl = Watchlist.objects.filter(user=user, name=watchlist_name).first()
            if not wl:
                self.stdout.write(self.style.WARNING(f"{user.username}: watchlist not found ({watchlist_name}). Try: python manage.py ensure_default_setup --username {user.username}"))
                continue

            inst_ids = list(
                InstrumentSelection.objects.filter(watchlist=wl, is_active=True).values_list("instrument_id", flat=True)
            )
            if not inst_ids:
                self.stdout.write(self.style.WARNING(f"{user.username}: watchlist '{watchlist_name}' has no active instrument selections. Try: python manage.py ensure_default_setup --username {user.username}"))
                continue

            instruments = list(Instrument.objects.filter(id__in=inst_ids, is_active=True).order_by("symbol"))
            if not instruments:
                self.stdout.write(self.style.WARNING(f"{user.username}: watchlist '{watchlist_name}' has no active instruments to scan. Seed/ingest instruments or run ensure_default_setup."))
                continue

            created_for_user = 0
            for cfg in configs:
                result = run_config(cfg, instruments=instruments, limit=limit, user=user, collect_diagnostics=verbose)
                if verbose:
                    assert isinstance(result, RunConfigResult)
                    created_for_user += result.created_count
                    self.stdout.write(
                        f"{user.username} | {cfg.strategy.name} [{cfg.timeframe}] watchlist={len(instruments)} data_ready={result.data_ready_count} scanned={result.scanned_count} created={result.created_count}"
                    )
                    for line in result.summary_lines:
                        self.stdout.write(f"  INFO {line}")
                    for item in result.results:
                        prefix = self.style.SUCCESS("CREATED") if item.created else self.style.WARNING("SKIPPED")
                        self.stdout.write(f"  {prefix} {item.symbol}: {item.reason}")
                else:
                    created_for_user += int(result)

            total_created += created_for_user
            self.stdout.write(f"{user.username}: created {created_for_user} signals")

        self.stdout.write(self.style.SUCCESS(f"Done. Total new signals: {total_created}"))
