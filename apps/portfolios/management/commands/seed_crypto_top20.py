from __future__ import annotations

import csv
import re
from pathlib import Path

import requests
from django.core.management.base import BaseCommand

from apps.marketdata.models import Instrument


DEFAULT_URL = "https://www.slickcharts.com/currency"


class Command(BaseCommand):
    help = "Seed Top-20 crypto instruments by market cap. Fetches from a URL by default; falls back to bundled sample list."

    def add_arguments(self, parser):
        parser.add_argument("--url", default=DEFAULT_URL, help="Source page URL (default: slickcharts currency page)")
        parser.add_argument("--file", default="", help="Optional local CSV path (overrides --url)")
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help="If set, deactivate CRYPTO instruments not found in the latest top-20 list.",
        )

    def handle(self, *args, **opts):
        items = None

        file_path = (opts.get("file") or "").strip()
        if file_path:
            items = _read_csv(Path(file_path))
            self.stdout.write(self.style.SUCCESS(f"Loaded crypto CSV from file: {file_path}"))
        else:
            url = (opts.get("url") or "").strip()
            try:
                html = _fetch(url)
                items = _parse_slickcharts_top20(html)
                self.stdout.write(self.style.SUCCESS(f"Fetched top-20 crypto list from URL: {url}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Failed to fetch/parse URL ({url}): {e}"))
                sample = Path(__file__).resolve().parent.parent.parent / "data" / "crypto_top20_sample.csv"
                items = _read_csv(sample)
                self.stdout.write(self.style.WARNING("Falling back to bundled sample top-20 crypto list."))

        symbols = set()
        created = updated = 0
        for it in items:
            symbol = (it.get("Symbol") or it.get("symbol") or "").strip().upper()
            name = (it.get("Name") or it.get("name") or "").strip()
            if not symbol:
                continue
            symbols.add(symbol)

            obj, was_created = Instrument.objects.update_or_create(
                symbol=symbol,
                defaults={
                    "name": name,
                    "asset_class": Instrument.AssetClass.CRYPTO,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        if opts.get("deactivate_missing"):
            qs = Instrument.objects.filter(asset_class=Instrument.AssetClass.CRYPTO).exclude(symbol__in=symbols)
            n = qs.update(is_active=False)
            self.stdout.write(self.style.WARNING(f"Deactivated {n} CRYPTO instruments not in latest list."))

        self.stdout.write(self.style.SUCCESS(f"Crypto seed complete: created={created} updated={updated}"))


def _fetch(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.text


def _parse_slickcharts_top20(html: str):
    # We look for patterns like "Bitcoin (BTC)" in the main table and take the first 20.
    # This is deliberately lightweight: no BeautifulSoup dependency.
    pat = re.compile(r">\s*([^<]+?)\s*\(([^)]+)\)\s*<")
    found = []
    for m in pat.finditer(html):
        name = m.group(1).strip()
        symbol = m.group(2).strip().upper()
        if not name or not symbol:
            continue
        # Filter out some obvious non-asset matches.
        if len(symbol) > 10:
            continue
        found.append({"Symbol": symbol, "Name": name})
        if len(found) >= 20:
            break
    if len(found) < 10:
        raise ValueError("Could not parse enough crypto symbols from source HTML")
    return found


def _read_csv(path: Path):
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
