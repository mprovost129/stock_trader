from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.marketdata.models import Instrument
from apps.portfolios.models import InstrumentSelection, UserRiskProfile, Watchlist
from apps.strategies.services.setup import ensure_default_setup


class Command(BaseCommand):
    help = "Create a default watchlist and risk profile for a user (safe helper for Milestone 1)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument(
            "--include",
            default="active",
            choices=["active", "all"],
            help="Which instruments to include in the default watchlist.",
        )
        parser.add_argument(
            "--account-equity",
            default="0",
            help="Account equity for sizing suggestions (stored locally; no broker connection)",
        )
        parser.add_argument(
            "--risk-pct",
            default="0.0025",
            help="Risk per trade as a decimal (0.0025 = 0.25%)",
        )
        parser.add_argument(
            "--ensure-default-strategy",
            action="store_true",
            help="Also create the starter strategy + run config if missing.",
        )

    def handle(self, *args, **opts):
        User = get_user_model()
        user = User.objects.filter(username=opts["username"]).first()
        if not user:
            self.stdout.write(self.style.ERROR("User not found."))
            return

        wl, _ = Watchlist.objects.get_or_create(user=user, name="Default", defaults={"is_active": True})
        if not wl.is_active:
            Watchlist.objects.filter(user=user, is_active=True).exclude(pk=wl.pk).update(is_active=False)
            wl.is_active = True
            wl.save(update_fields=["is_active", "updated_at"])

        if opts["include"] == "all":
            instruments = Instrument.objects.all()
        else:
            instruments = Instrument.objects.filter(is_active=True)

        created = 0
        for inst in instruments:
            _, was_created = InstrumentSelection.objects.get_or_create(watchlist=wl, instrument=inst)
            if was_created:
                created += 1

        rp, _ = UserRiskProfile.objects.get_or_create(user=user)
        rp.account_equity = opts["account_equity"]
        rp.risk_per_trade_pct = opts["risk_pct"]
        rp.save(update_fields=["account_equity", "risk_per_trade_pct", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"Bootstrapped {user.username}: watchlist items added={created}"))

        if opts.get("ensure_default_strategy"):
            setup_result = ensure_default_setup(
                username=user.username,
                account_equity=opts["account_equity"],
                risk_pct=opts["risk_pct"],
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Starter strategy ensured: "
                    f"strategy_created={setup_result.strategy_created} run_config_created={setup_result.run_config_created}"
                )
            )
