from __future__ import annotations

import csv
from pathlib import Path

import requests
from django.core.management.base import BaseCommand

from apps.marketdata.models import Instrument


DEFAULT_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"


class Command(BaseCommand):
    help = "Seed S&P 500 instruments. Fetches from a URL by default; falls back to bundled sample list."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            default=DEFAULT_URL,
            help="CSV URL with columns like Symbol,Name,Sector (default: datasets/s-and-p-500-companies)",
        )
        parser.add_argument(
            "--file",
            default="",
            help="Optional local CSV path (overrides --url)",
        )
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help="If set, deactivate STOCK instruments not found in the latest S&P 500 list.",
        )

    def handle(self, *args, **opts):
        rows = None

        file_path = (opts.get("file") or "").strip()
        if file_path:
            rows = _read_csv(Path(file_path))
            self.stdout.write(self.style.SUCCESS(f"Loaded S&P 500 CSV from file: {file_path}"))
        else:
            url = (opts.get("url") or "").strip()
            try:
                rows = _read_csv_from_url(url)
                self.stdout.write(self.style.SUCCESS(f"Fetched S&P 500 CSV from URL: {url}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Failed to fetch URL ({url}): {e}"))
                sample = Path(__file__).resolve().parent.parent.parent / "data" / "sp500_sample.csv"
                rows = _read_csv(sample)
                self.stdout.write(self.style.WARNING("Falling back to bundled sample S&P 500 list."))

        symbols = set()
        created = updated = 0
        for r in rows:
            symbol = (r.get("Symbol") or r.get("symbol") or "").strip()
            name = (r.get("Name") or r.get("name") or "").strip()
            if not symbol:
                continue
            symbols.add(symbol.upper())

            obj, was_created = Instrument.objects.update_or_create(
                symbol=symbol.upper(),
                defaults={
                    "name": name,
                    "asset_class": Instrument.AssetClass.STOCK,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        if opts.get("deactivate_missing"):
            qs = Instrument.objects.filter(asset_class=Instrument.AssetClass.STOCK).exclude(symbol__in=symbols)
            n = qs.update(is_active=False)
            self.stdout.write(self.style.WARNING(f"Deactivated {n} STOCK instruments not in latest list."))

        self.stdout.write(self.style.SUCCESS(f"S&P 500 seed complete: created={created} updated={updated}"))


def _read_csv(path: Path):
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _read_csv_from_url(url: str):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    text = r.text
    return list(csv.DictReader(text.splitlines()))
