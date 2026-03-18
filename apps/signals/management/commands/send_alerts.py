from __future__ import annotations

from collections import Counter

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.signals.services.alerts import deliver_enabled_alerts, explain_alert_eligibility, get_alert_candidates, get_enabled_delivery_channels


class Command(BaseCommand):
    help = "Send disciplined alerts for NEW signals with trade plans across all enabled channels."

    def add_arguments(self, parser):
        parser.add_argument("--username", help="Optional username filter.")
        parser.add_argument("--dry-run", action="store_true", help="Evaluate and record dry-run deliveries without posting.")

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        dry_run = bool(options.get("dry_run"))

        if username:
            User = get_user_model()
            if not User.objects.filter(username=username).exists():
                raise CommandError(f"User not found: {username}")

        channels = get_enabled_delivery_channels()
        if not channels:
            self.stdout.write(self.style.WARNING("No alert delivery channels are enabled."))
            return

        candidates = list(get_alert_candidates(username=username or None))
        if not candidates:
            self.stdout.write(self.style.WARNING("No alert candidates found."))
            return

        counts = Counter()
        for signal in candidates:
            outcomes = deliver_enabled_alerts(signal=signal, dry_run=dry_run)
            explanation = explain_alert_eligibility(signal=signal)
            for outcome in outcomes:
                delivery = outcome.delivery
                counts[delivery.status] += 1
                counts[f"channel:{delivery.channel}"] += 1
                counts[f"reason:{delivery.reason}"] += 1
                extra = ""
                if explanation.score_value is not None and explanation.score_threshold is not None:
                    extra = f" score={explanation.score_value:.2f}/100 threshold={explanation.score_threshold:.2f}/100 gap={explanation.score_gap:+.2f}"
                elif explanation.age_minutes is not None and explanation.freshness_limit_minutes is not None and delivery.reason == "stale_signal":
                    extra = f" age={explanation.age_minutes}m max_age={explanation.freshness_limit_minutes}m"
                self.stdout.write(
                    f"[{delivery.channel}] {signal.instrument.symbol} {signal.direction} {signal.timeframe} {signal.signal_label or signal.signal_kind}: {delivery.status} ({delivery.reason}){extra}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Done. "
                f"sent={counts.get('SENT', 0)} dry_run={counts.get('DRY_RUN', 0)} "
                f"skipped={counts.get('SKIPPED', 0)} failed={counts.get('FAILED', 0)}"
            )
        )
        self.stdout.write("Channel summary:")
        for channel in channels:
            self.stdout.write(f"  {channel}: {counts.get(f'channel:{channel}', 0)}")
