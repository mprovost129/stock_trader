from django.core.management.base import BaseCommand

from apps.marketdata.models import Instrument
from apps.marketdata.services.ingestion import ingest_from_csv, ingest_from_provider


class Command(BaseCommand):
    help = "Ingest price bars from CSV or a configured market data provider."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", help="Symbol to ingest (must exist as an Instrument)", required=True)
        parser.add_argument("--timeframe", default="1d", help="Timeframe: 1m, 5m, 1d")
        parser.add_argument("--limit", type=int, default=300, help="Number of bars (provider mode)")
        parser.add_argument(
            "--csv",
            dest="csv_path",
            help="Optional path to a CSV file with header ts,open,high,low,close,volume (UTC timestamps recommended)",
            required=False,
        )
        parser.add_argument(
            "--provider",
            dest="provider_name",
            help="Optional provider override. Stocks: yahoo or polygon. Crypto: coinbase, kraken, or binance.",
            required=False,
        )

    def handle(self, *args, **options):
        symbol = options["symbol"].upper().strip()
        timeframe = options["timeframe"].strip().lower()
        limit = int(options["limit"])
        csv_path = options.get("csv_path")
        provider_name = options.get("provider_name")

        if not Instrument.objects.filter(symbol=symbol).exists():
            self.stdout.write(self.style.ERROR(f"Unknown instrument symbol: {symbol}"))
            self.stdout.write("Seed instruments first (e.g., seed_sp500 / seed_crypto_top20).")
            return

        try:
            if csv_path:
                res = ingest_from_csv(symbol=symbol, timeframe=timeframe, csv_path=csv_path)
                self.stdout.write(self.style.SUCCESS(
                    f"CSV ingest complete for {symbol} {timeframe}: created={res.created} updated={res.updated}"))
            else:
                res = ingest_from_provider(symbol=symbol, timeframe=timeframe, limit=limit, provider_name=provider_name)
                suffix = f" provider={provider_name}" if provider_name else ""
                self.stdout.write(self.style.SUCCESS(
                    f"Provider ingest complete for {symbol} {timeframe}{suffix}: created={res.created} updated={res.updated}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Ingest failed: {e}"))
