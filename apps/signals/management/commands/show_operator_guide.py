from django.core.management.base import BaseCommand


GUIDE = r'''
Trading Advisor — Operator Guide

1) First-time setup
   python manage.py migrate
   python manage.py createsuperuser
   python manage.py ensure_default_setup --username <your_username> --account-equity 25000 --risk-pct 0.0025

2) Optional policy preset
   python manage.py show_operator_policy
   python manage.py apply_operator_preset balanced

   Restart your shell or dev server after applying a preset so .env values reload.

3) Prove Discord is wired
   python manage.py send_test_alert --dry-run
   python manage.py send_test_alert

4) Backfill market data safely
   python manage.py ingest_watchlist_prices --username <your_username> --max-symbols 5 --throttle-seconds 12

5) See what the market looks like
   python manage.py preview_market_regime --username <your_username> --limit 20
   python manage.py run_scans --username <your_username> --verbose
   python manage.py preview_alert_queue --username <your_username> --limit 20
   python manage.py preview_next_session_queue --username <your_username> --limit 10

6) Run the full operator cycle
   Safe validation:
   python manage.py run_operator_cycle --username <your_username> --dry-run --verbose-scan --max-symbols 5 --throttle-seconds 12

   Live Discord delivery when eligible:
   python manage.py run_operator_cycle --username <your_username> --verbose-scan --max-symbols 5 --throttle-seconds 12

7) Paper trade workflow
   Open from a signal:
   python manage.py open_paper_trade --signal-id <signal_id> --username <your_username>

   Monitor open positions:
   python manage.py monitor_positions --username <your_username> --dry-run

   Close a trade:
   python manage.py close_paper_trade --trade-id <trade_id> --exit-price 123.45

8) Outcome + analytics
   python manage.py evaluate_signal_outcomes --username <your_username> --lookahead-bars 5 --only-missing
   python manage.py analyze_trade_performance --username <your_username>

9) How to interpret a quiet system
   - If run_scans says created=0, no fresh qualifying signals occurred.
   - If send_alerts says No alert candidates found, policy may be blocking delivery
     (session, low score, cooldown, stale signal, duplicate send) or nothing fresh exists.
   - Use preview_alert_queue and preview_next_session_queue to see what is blocked and why.

10) Portfolio health snapshots
   Take a manual snapshot:
   python manage.py save_portfolio_health_snapshot --username <your_username>

   Check for deterioration vs. the previous snapshot:
   python manage.py check_portfolio_health_deterioration --username <your_username> --dry-run
   python manage.py check_portfolio_health_deterioration --username <your_username>

   The scheduler does both automatically every SCHEDULER_PORTFOLIO_SNAPSHOT_EVERY cycles (default: 4).

11) Current operating model
   - The app generates signals and Discord alerts.
   - You decide and place trades manually in Robinhood or another broker.
   - Open paper trades can trigger deterioration warnings and trend-reversal warnings.
   - Portfolio health is scored, snapshotted, and monitored automatically by the scheduler.
   - Broker automation can be added later without redesigning the system.
'''


class Command(BaseCommand):
    help = "Print the operator guide for day-to-day use of the system."

    def handle(self, *args, **options):
        self.stdout.write(GUIDE)
