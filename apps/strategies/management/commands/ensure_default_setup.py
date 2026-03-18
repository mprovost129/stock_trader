from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.strategies.services.setup import DEFAULT_STARTER_SYMBOLS, ensure_default_setup


class Command(BaseCommand):
    help = "Create the default watchlist, starter instruments, starter strategy, and active run config for a user."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--watchlist", default="Default")
        parser.add_argument(
            "--symbols",
            default=",".join(DEFAULT_STARTER_SYMBOLS),
            help="Comma-separated starter symbols to ensure on the watchlist.",
        )
        parser.add_argument("--account-equity", default="25000")
        parser.add_argument("--risk-pct", default="0.0025")

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        symbols = [part.strip().upper() for part in (options.get("symbols") or "").split(",") if part.strip()]
        try:
            result = ensure_default_setup(
                username=username,
                watchlist_name=(options.get("watchlist") or "Default").strip() or "Default",
                starter_symbols=symbols,
                account_equity=options.get("account_equity"),
                risk_pct=options.get("risk_pct"),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Default setup ensured for {result.username}"))
        self.stdout.write(
            f"watchlist={result.watchlist_name} created={result.watchlist_created} selections_added={result.selections_added} instruments_created={result.instruments_created}"
        )
        self.stdout.write(
            f"strategy_created={result.strategy_created} run_config_created={result.run_config_created} risk_profile_created={result.risk_profile_created}"
        )
