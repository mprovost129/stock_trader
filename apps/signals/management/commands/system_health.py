from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Max
from django.utils import timezone

from apps.marketdata.models import PriceBar
from apps.portfolios.models import InstrumentSelection, Watchlist
from apps.signals.models import AlertDelivery, PaperTrade, PositionAlert, Signal, SignalOutcome


class Command(BaseCommand):
    help = "Show a compact system-health summary for a user's trading workflow."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--watchlist", default="Default")
        parser.add_argument("--timeframe", default="1d")

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        watchlist_name = (options.get("watchlist") or "Default").strip() or "Default"
        timeframe = (options.get("timeframe") or "1d").strip().lower()

        wl = Watchlist.objects.filter(user__username=username, name=watchlist_name).first()
        if not wl:
            raise CommandError(f"Watchlist not found for {username}: {watchlist_name}")

        instrument_ids = list(
            InstrumentSelection.objects.filter(watchlist=wl, is_active=True, instrument__is_active=True)
            .values_list("instrument_id", flat=True)
        )
        selected_count = len(instrument_ids)
        ready_qs = PriceBar.objects.filter(instrument_id__in=instrument_ids, timeframe=timeframe).values("instrument_id").annotate(latest_ts=Max("ts"))
        ready_count = ready_qs.count()
        oldest_latest_ts = None
        newest_latest_ts = None
        rows = list(ready_qs)
        if rows:
            latest_values = [row["latest_ts"] for row in rows if row.get("latest_ts")]
            if latest_values:
                oldest_latest_ts = min(latest_values)
                newest_latest_ts = max(latest_values)

        now = timezone.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

        signals_today = Signal.objects.filter(created_by__username=username, created_at__gte=start_of_day).count()
        new_signals = Signal.objects.filter(created_by__username=username, status=Signal.Status.NEW).count()
        alerts_today = AlertDelivery.objects.filter(signal__created_by__username=username, created_at__gte=start_of_day, status=AlertDelivery.Status.SENT).count()
        alerts_skipped_today = AlertDelivery.objects.filter(signal__created_by__username=username, created_at__gte=start_of_day, status=AlertDelivery.Status.SKIPPED).count()
        open_trades = PaperTrade.objects.filter(opened_by__username=username, status=PaperTrade.Status.OPEN).count()
        closed_trades_today = PaperTrade.objects.filter(opened_by__username=username, status=PaperTrade.Status.CLOSED, updated_at__gte=start_of_day).count()
        position_alerts_today = PositionAlert.objects.filter(paper_trade__opened_by__username=username, created_at__gte=start_of_day).count()
        pending_outcomes = SignalOutcome.objects.filter(signal__created_by__username=username, status=SignalOutcome.Status.PENDING).count()

        self.stdout.write(self.style.SUCCESS(f"System health for {username}/{watchlist_name} [{timeframe}]"))
        self.stdout.write(f"Watchlist symbols: {selected_count}")
        self.stdout.write(f"Symbols with bars: {ready_count}")
        self.stdout.write(f"Signals today: {signals_today} (NEW total: {new_signals})")
        self.stdout.write(f"Alerts sent today: {alerts_today} | skipped today: {alerts_skipped_today}")
        self.stdout.write(f"Paper trades open: {open_trades} | closed today: {closed_trades_today}")
        self.stdout.write(f"Position alerts today: {position_alerts_today}")
        self.stdout.write(f"Pending signal outcomes: {pending_outcomes}")
        if newest_latest_ts:
            self.stdout.write(f"Freshest bar timestamp: {timezone.localtime(newest_latest_ts).strftime('%Y-%m-%d %I:%M %p %Z')}")
        if oldest_latest_ts and oldest_latest_ts != newest_latest_ts:
            self.stdout.write(f"Oldest latest-bar timestamp in coverage: {timezone.localtime(oldest_latest_ts).strftime('%Y-%m-%d %I:%M %p %Z')}")
