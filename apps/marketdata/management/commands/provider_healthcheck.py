from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.marketdata.services.health import provider_healthcheck


class Command(BaseCommand):
    help = "Run first-pass provider health checks for the currently supported market-data providers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--providers",
            default="yahoo,coinbase,polygon",
            help="Comma-separated providers to probe (default: yahoo,coinbase,polygon).",
        )

    def handle(self, *args, **options):
        providers = [p.strip().lower() for p in (options.get("providers") or "").split(",") if p.strip()]
        if not providers:
            self.stdout.write(self.style.WARNING("No providers selected."))
            return

        failures = 0
        for provider in providers:
            result = provider_healthcheck(provider=provider)
            if result.ok:
                self.stdout.write(self.style.SUCCESS(f"{result.provider}: {result.message}"))
            else:
                failures += 1
                self.stdout.write(self.style.WARNING(f"{result.provider}: {result.message}"))

        if failures:
            self.stdout.write(self.style.WARNING(f"Health checks completed with {failures} failure(s)."))
        else:
            self.stdout.write(self.style.SUCCESS("All requested provider health checks passed."))
