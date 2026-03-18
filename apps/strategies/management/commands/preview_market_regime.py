
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.marketdata.models import PriceBar
from apps.marketdata.services.indicators import current_market_regime
from apps.portfolios.models import InstrumentSelection, Watchlist


class Command(BaseCommand):
    help = "Show the current market regime for data-ready symbols in a user's watchlist."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--watchlist", default="Default")
        parser.add_argument("--timeframe", default="1d")
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        username = options["username"]
        watchlist_name = options.get("watchlist") or "Default"
        timeframe = options.get("timeframe") or "1d"
        limit = int(options.get("limit") or 20)

        wl = Watchlist.objects.filter(user__username=username, name=watchlist_name).first()
        if not wl:
            raise CommandError(f"Watchlist not found for {username}: {watchlist_name}")

        selections = InstrumentSelection.objects.select_related("instrument").filter(watchlist=wl, is_active=True, instrument__is_active=True).order_by("instrument__symbol")
        rows: list[tuple[str, str, str]] = []
        for sel in selections:
            bars_qs = PriceBar.objects.filter(instrument=sel.instrument, timeframe=timeframe).order_by("-ts").only("close", "high", "low", "volume")[:60]
            bars = list(reversed(list(bars_qs)))
            if len(bars) < 25:
                continue
            closes = [b.close for b in bars]
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]
            volumes = [b.volume for b in bars]
            regime = current_market_regime(closes=closes, highs=highs, lows=lows, volumes=volumes)
            rows.append((sel.instrument.symbol, sel.instrument.asset_class, regime))
            if len(rows) >= limit:
                break

        if not rows:
            self.stdout.write("No data-ready symbols found for regime preview.")
            return

        self.stdout.write(f"Market regime preview for {username}/{watchlist_name} [{timeframe}]")
        for symbol, asset_class, regime in rows:
            self.stdout.write(f"- {symbol} ({asset_class}): {regime}")
