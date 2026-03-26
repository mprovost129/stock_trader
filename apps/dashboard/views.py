from collections import defaultdict
import logging
from time import perf_counter

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Avg, Count, Q
from django.shortcuts import redirect, render
from django.utils.http import urlencode

from apps.journal.models import JournalEntry
from apps.portfolios.models import HeldPosition, SavedFilterPreset, UserRiskProfile
from apps.portfolios.watchlists import ensure_active_watchlist
from apps.portfolios.services import assess_signal_guardrails, build_holding_health_snapshot, build_signal_correlation_context, summarize_account_drawdown_monitoring, summarize_account_exposure_heatmap, summarize_account_holding_queues, summarize_account_retention_override_posture, summarize_account_retention_template_drift, summarize_account_risk_posture, summarize_account_stop_guardrails, summarize_broker_snapshot_posture, summarize_evidence_lifecycle_automation, summarize_holding_performance, summarize_holding_risk_guardrails, summarize_holding_sector_exposure, summarize_open_holdings, summarize_portfolio_exposure, summarize_portfolio_health_history, summarize_portfolio_health_score, summarize_stop_discipline_history, summarize_stop_discipline_trends, summarize_stop_policy_exception_trends, summarize_stop_policy_timeliness, summarize_watchlist_sectors
from apps.marketdata.models import IngestionJob, Instrument, PriceBar
from apps.marketdata.services.freshness import build_data_freshness_summary
from apps.marketdata.services.ingestion_queue import enqueue_watchlist_ingest_job
from apps.marketdata.services.ingestion_state import clear_provider_cooldowns, clear_unsupported_crypto_symbols
from apps.signals.models import AlertDelivery, OperatorNotification, PaperTrade, PositionAlert, Signal, SignalOutcome
from apps.signals.services.alerts import build_alert_queue_preview, build_next_session_queue, build_tuning_preview, get_enabled_delivery_channels
from apps.signals.services.delivery_health import get_delivery_health_summary
from apps.signals.services.lifecycle import get_trade_lifecycle_summary
from apps.strategies.models import StrategyRunConfig
from apps.signals.services.position_monitor import rank_open_positions
from apps.signals.views import _extract_signal_filter_params
from apps.portfolios.views import _extract_holding_filter_params




logger = logging.getLogger("apps.dashboard")


def _normalize_score(score):
    if score is None:
        return None
    value = float(score)
    if value <= 1:
        value *= 100
    return max(0.0, min(100.0, value))


def _score_bucket_label(score):
    value = _normalize_score(score)
    if value is None:
        return "Unscored"
    if value >= 100:
        return "100"
    lower = int(value // 10) * 10
    upper = lower + 9
    return f"{lower:02d}-{upper:02d}"


def _bucket_sort_key(label: str):
    if label == "Unscored":
        return 999
    if label == "100":
        return 100
    return int(label.split('-')[0])


def _safe_pct(numerator: int, denominator: int):
    if not denominator:
        return None
    return round((numerator / denominator) * 100, 1)


def _verdict_from_win_rate(win_rate):
    if win_rate is None:
        return "thin"
    if win_rate >= 65:
        return "hot"
    if win_rate >= 50:
        return "working"
    return "cold"


def _build_trade_analytics(*, user, timeframe: str = "", strategy: str = "", min_count: int = 1):
    closed_trades_qs = PaperTrade.objects.select_related("signal", "signal__strategy").filter(
        opened_by=user,
        status=PaperTrade.Status.CLOSED,
    )
    evaluated_outcomes_qs = SignalOutcome.objects.select_related("signal", "signal__strategy").filter(
        status=SignalOutcome.Status.EVALUATED,
        signal__created_by=user,
    )

    if timeframe:
        closed_trades_qs = closed_trades_qs.filter(signal__timeframe=timeframe)
        evaluated_outcomes_qs = evaluated_outcomes_qs.filter(signal__timeframe=timeframe)
    if strategy:
        closed_trades_qs = closed_trades_qs.filter(signal__strategy__slug=strategy)
        evaluated_outcomes_qs = evaluated_outcomes_qs.filter(signal__strategy__slug=strategy)

    paper_bucket_map = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "avg_pnl_seed": []})
    paper_strategy_map = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "avg_pnl_seed": []})
    paper_timeframe_map = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "avg_pnl_seed": []})

    for trade in closed_trades_qs:
        signal = trade.signal
        bucket = _score_bucket_label(getattr(signal, "score", None))
        strategy_slug = getattr(signal.strategy, "slug", "—")
        timeframe_value = signal.timeframe or "—"
        won = trade.pnl_amount is not None and trade.pnl_amount > 0
        lost = trade.pnl_amount is not None and trade.pnl_amount < 0
        for key, mapping in ((bucket, paper_bucket_map), (strategy_slug, paper_strategy_map), (timeframe_value, paper_timeframe_map)):
            mapping[key]["count"] += 1
            if won:
                mapping[key]["wins"] += 1
            elif lost:
                mapping[key]["losses"] += 1
            if trade.pnl_pct is not None:
                mapping[key]["avg_pnl_seed"].append(float(trade.pnl_pct))

    outcome_bucket_map = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "mixed": 0, "flat": 0, "avg_return_seed": []})
    outcome_strategy_map = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "mixed": 0, "flat": 0, "avg_return_seed": []})

    for outcome in evaluated_outcomes_qs:
        signal = outcome.signal
        bucket = _score_bucket_label(getattr(signal, "score", None))
        strategy_slug = getattr(signal.strategy, "slug", "—")
        for key, mapping in ((bucket, outcome_bucket_map), (strategy_slug, outcome_strategy_map)):
            mapping[key]["count"] += 1
            if outcome.outcome_label == SignalOutcome.OutcomeLabel.WIN:
                mapping[key]["wins"] += 1
            elif outcome.outcome_label == SignalOutcome.OutcomeLabel.LOSS:
                mapping[key]["losses"] += 1
            elif outcome.outcome_label == SignalOutcome.OutcomeLabel.MIXED:
                mapping[key]["mixed"] += 1
            elif outcome.outcome_label == SignalOutcome.OutcomeLabel.FLAT:
                mapping[key]["flat"] += 1
            if outcome.return_pct is not None:
                mapping[key]["avg_return_seed"].append(float(outcome.return_pct))

    def finalize(mapping, *, include_outcome_fields=False, sort_mode="label"):
        rows = []
        for label, item in mapping.items():
            if item["count"] < min_count:
                continue
            row = {
                "label": label,
                "count": item["count"],
                "wins": item["wins"],
                "losses": item["losses"],
                "win_rate": _safe_pct(item["wins"], item["count"]),
                "verdict": _verdict_from_win_rate(_safe_pct(item["wins"], item["count"])),
            }
            if include_outcome_fields:
                avg_seed = item["avg_return_seed"]
                row["mixed"] = item["mixed"]
                row["flat"] = item["flat"]
                row["avg_return_pct"] = round(sum(avg_seed) / len(avg_seed), 2) if avg_seed else None
            else:
                avg_seed = item["avg_pnl_seed"]
                row["avg_pnl_pct"] = round(sum(avg_seed) / len(avg_seed), 2) if avg_seed else None
            rows.append(row)
        if sort_mode == "score_bucket":
            rows.sort(key=lambda row: _bucket_sort_key(row["label"]))
        elif sort_mode == "count_desc":
            rows.sort(key=lambda row: (-row["count"], row["label"]))
        else:
            rows.sort(key=lambda row: row["label"])
        return rows

    paper_by_bucket = finalize(paper_bucket_map, sort_mode="score_bucket")
    paper_by_strategy = finalize(paper_strategy_map, sort_mode="count_desc")
    paper_by_timeframe = finalize(paper_timeframe_map, sort_mode="count_desc")
    outcomes_by_bucket = finalize(outcome_bucket_map, include_outcome_fields=True, sort_mode="score_bucket")
    outcomes_by_strategy = finalize(outcome_strategy_map, include_outcome_fields=True, sort_mode="count_desc")

    paper_total = sum(row["count"] for row in paper_by_bucket)
    paper_wins = sum(row["wins"] for row in paper_by_bucket)
    paper_avg_seed = [row["avg_pnl_pct"] for row in paper_by_bucket if row.get("avg_pnl_pct") is not None]
    outcomes_total = sum(row["count"] for row in outcomes_by_bucket)
    outcomes_wins = sum(row["wins"] for row in outcomes_by_bucket)
    outcomes_avg_seed = [row["avg_return_pct"] for row in outcomes_by_bucket if row.get("avg_return_pct") is not None]

    best_bucket = None
    if paper_by_bucket:
        eligible = [row for row in paper_by_bucket if row["count"] >= max(2, min_count)]
        if eligible:
            best_bucket = sorted(eligible, key=lambda row: (-(row["win_rate"] or -1), -row["count"], row["label"]))[0]

    return {
        "paper_by_bucket": paper_by_bucket,
        "paper_by_strategy": paper_by_strategy,
        "paper_by_timeframe": paper_by_timeframe,
        "outcomes_by_bucket": outcomes_by_bucket,
        "outcomes_by_strategy": outcomes_by_strategy,
        "filters": {"timeframe": timeframe, "strategy": strategy, "min_count": min_count},
        "summary": {
            "paper_closed_count": paper_total,
            "paper_win_rate": _safe_pct(paper_wins, paper_total),
            "paper_avg_pnl_pct": round(sum(paper_avg_seed) / len(paper_avg_seed), 2) if paper_avg_seed else None,
            "outcomes_evaluated_count": outcomes_total,
            "outcomes_win_rate": _safe_pct(outcomes_wins, outcomes_total),
            "outcomes_avg_return_pct": round(sum(outcomes_avg_seed) / len(outcomes_avg_seed), 2) if outcomes_avg_seed else None,
            "best_paper_bucket": best_bucket,
        },
    }

def _build_signal_preset_widgets(user):
    widgets = []
    held_ids = list(
        HeldPosition.objects.filter(user=user, status=HeldPosition.Status.OPEN)
        .values_list("instrument_id", flat=True)
        .distinct()
    )
    presets = SavedFilterPreset.objects.filter(
        user=user,
        scope=SavedFilterPreset.Scope.SIGNALS,
        is_dashboard_widget=True,
    ).order_by("name")
    for preset in presets:
        filters = _extract_signal_filter_params(preset.filters or {})
        qs = Signal.objects.filter(created_by=user).exclude(direction=Signal.Direction.FLAT)
        status = filters.get("status")
        if status:
            qs = qs.filter(status=status)
        strategy = filters.get("strategy")
        if strategy:
            qs = qs.filter(strategy__slug=strategy)
        instrument = filters.get("instrument")
        if instrument:
            qs = qs.filter(instrument__symbol=instrument)
        direction = filters.get("direction")
        if direction:
            qs = qs.filter(direction=direction)
        timeframe = filters.get("timeframe")
        if timeframe:
            qs = qs.filter(timeframe=timeframe)
        ownership_state = filters.get("ownership_state")
        if ownership_state == "HELD_OPEN":
            qs = qs.filter(instrument_id__in=held_ids)
        elif ownership_state == "NOT_HELD":
            qs = qs.exclude(instrument_id__in=held_ids)
        if filters.get("outcome_status"):
            qs = qs.filter(outcome__status=filters["outcome_status"])
        if filters.get("review_queue"):
            qs = qs.filter(status=Signal.Status.NEW).exclude(outcome__status=SignalOutcome.Status.EVALUATED)
        widgets.append({
            "preset": preset,
            "count": qs.count(),
            "querystring": urlencode({"preset": preset.pk}),
            "filters": filters,
        })
    return widgets


def _build_holding_preset_widgets(user):
    widgets = []
    presets = SavedFilterPreset.objects.filter(
        user=user,
        scope=SavedFilterPreset.Scope.HOLDINGS,
        is_dashboard_widget=True,
    ).order_by("name")
    for preset in presets:
        filters = _extract_holding_filter_params(preset.filters or {})
        status = filters.get("status")
        base_qs = HeldPosition.objects.select_related("instrument").filter(user=user)
        if status:
            base_qs = base_qs.filter(status=status)
        positions = list(base_qs)
        source = filters.get("source")
        if source:
            positions = [item for item in positions if item.source == source]
        reconciliation = filters.get("reconciliation")
        if reconciliation == "MISSING_IMPORT":
            positions = [item for item in positions if item.missing_from_latest_import]
        elif reconciliation == "IN_SYNC_IMPORT":
            positions = [item for item in positions if not item.missing_from_latest_import]
        recommendation = filters.get("recommendation")
        if recommendation:
            allowed = []
            for item in positions:
                if item.status != HeldPosition.Status.OPEN:
                    continue
                snapshot = build_holding_health_snapshot(item)
                if snapshot.recommendation_code == recommendation:
                    allowed.append(item)
            positions = allowed
        widgets.append({
            "preset": preset,
            "count": len(positions),
            "querystring": urlencode({"preset": preset.pk}),
            "filters": filters,
        })
    return widgets


@login_required
def home(request):
    started = perf_counter()
    timings_ms: dict[str, int] = {}

    def _mark(name: str, t0: float) -> None:
        timings_ms[name] = int((perf_counter() - t0) * 1000)
    # ── FAST DASHBOARD (only mode) ────────────────────────────────

    t0 = perf_counter()
    user_signals = Signal.objects.filter(created_by=request.user)
    signals = (
        user_signals.select_related("instrument", "strategy")
        .order_by("-generated_at")
        .all()[:25]
    )
    top_opportunities = list(
        user_signals.select_related("instrument", "strategy")
        .exclude(direction=Signal.Direction.FLAT)
        .filter(status__in=[Signal.Status.NEW, Signal.Status.REVIEWED, Signal.Status.TAKEN])
        .order_by("-score", "-generated_at")[:8]
    )
    _mark("signals", t0)

    t0 = perf_counter()
    watchlist = ensure_active_watchlist(request.user)
    watchlist_count = 0
    data_ready_count = 0
    watchlist_priority_counts = {"HIGH": 0, "NORMAL": 0, "LOW": 0}
    watchlist_sector_board = []
    if watchlist:
        active_qs = watchlist.selections.filter(is_active=True, instrument__is_active=True)
        watchlist_count = active_qs.count()
        watchlist_priority_counts = {
            "HIGH": active_qs.filter(priority="HIGH").count(),
            "NORMAL": active_qs.filter(priority="NORMAL").count(),
            "LOW": active_qs.filter(priority="LOW").count(),
        }
        instrument_ids = list(active_qs.values_list("instrument_id", flat=True))
        data_ready_count = PriceBar.objects.filter(instrument_id__in=instrument_ids, timeframe="1d").values("instrument_id").distinct().count()
        watchlist_sector_board = summarize_watchlist_sectors(watchlist=watchlist, user=request.user, limit=5)
    ingestion_backlog_count = max(watchlist_count - data_ready_count, 0)
    _mark("watchlist", t0)

    t0 = perf_counter()
    signal_counts = user_signals.aggregate(
        total=Count("id"),
        new=Count("id", filter=Q(status=Signal.Status.NEW)),
        confirmed=Count("id", filter=Q(status=Signal.Status.CONFIRMED)),
    )
    journal_counts = JournalEntry.objects.filter(user=request.user).aggregate(
        total=Count("id"),
        wins=Count("id", filter=Q(outcome=JournalEntry.Outcome.WIN)),
        losses=Count("id", filter=Q(outcome=JournalEntry.Outcome.LOSS)),
    )
    active_configs = StrategyRunConfig.objects.filter(is_active=True, strategy__is_enabled=True).count()
    recent_outcomes = list(
        SignalOutcome.objects.select_related("signal", "signal__instrument")
        .filter(signal__created_by=request.user)
        .exclude(status=SignalOutcome.Status.PENDING)
        .order_by("-updated_at")[:5]
    )
    review_queue = list(
        user_signals.select_related("instrument", "strategy")
        .filter(status=Signal.Status.NEW)
        .exclude(direction=Signal.Direction.FLAT)
        .order_by("-score", "-generated_at")[:10]
    )
    pending_outcome_count = user_signals.filter(status=Signal.Status.NEW).exclude(outcome__status=SignalOutcome.Status.EVALUATED).count()

    # Quick held-position summary (no refresh calls — just read stored values)
    open_positions_qs = HeldPosition.objects.filter(user=request.user, status=HeldPosition.Status.OPEN)
    held_open_count = open_positions_qs.count()
    held_sell_now = open_positions_qs.filter(recommendation="SELL_NOW").count()
    profile = UserRiskProfile.objects.filter(user=request.user).first()

    context = {
        "signals": signals,
        "watchlist": watchlist,
        "watchlist_count": watchlist_count,
        "watchlist_priority_counts": watchlist_priority_counts,
        "watchlist_sector_board": watchlist_sector_board,
        "data_ready_count": data_ready_count,
        "signal_counts": signal_counts,
        "ingestion_backlog_count": ingestion_backlog_count,
        "journal_counts": journal_counts,
        "active_configs": active_configs,
        "top_opportunities": top_opportunities,
        "review_queue": review_queue,
        "recent_outcomes": recent_outcomes,
        "pending_outcome_count": pending_outcome_count,
        "held_open_count": held_open_count,
        "held_sell_now": held_sell_now,
        "account_equity": profile.account_equity if profile else None,
    }
    total_ms = int((perf_counter() - started) * 1000)
    slow_ms = int(getattr(settings, "DASHBOARD_HOME_SLOW_MS", 2000) or 2000)
    if bool(getattr(settings, "DASHBOARD_HOME_TRACE", False)) or total_ms >= slow_ms:
        logger.info(
            "dashboard.home timing user=%s total_ms=%s sections=%s",
            request.user.username,
            total_ms,
            ",".join(f"{k}:{v}" for k, v in sorted(timings_ms.items(), key=lambda item: item[1], reverse=True)),
        )
    return render(request, "dashboard/home.html", context)


@login_required
def analytics(request):
    timeframe = (request.GET.get("timeframe") or "").strip()
    strategy = (request.GET.get("strategy") or "").strip()
    min_count_raw = (request.GET.get("min_count") or "1").strip()
    try:
        min_count = max(1, int(min_count_raw))
    except ValueError:
        min_count = 1

    analytics_data = _build_trade_analytics(user=request.user, timeframe=timeframe, strategy=strategy, min_count=min_count)
    timeframe_choices = list(
        Signal.objects.filter(created_by=request.user).order_by().values_list("timeframe", flat=True).distinct()
    )
    strategy_choices = list(
        Signal.objects.filter(created_by=request.user).order_by().values_list("strategy__slug", flat=True).distinct()
    )
    return render(request, "dashboard/analytics.html", {
        "analytics": analytics_data,
        "timeframe": timeframe,
        "strategy": strategy,
        "min_count": min_count,
        "timeframe_choices": timeframe_choices,
        "strategy_choices": strategy_choices,
    })


@login_required
def data_freshness(request):
    timeframe = (request.GET.get("timeframe") or "1d").strip().lower()
    if timeframe not in {"1m", "5m", "1d"}:
        timeframe = "1d"
    watchlist = ensure_active_watchlist(request.user)
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        symbols = []
        if watchlist:
            symbols = list(
                watchlist.selections.filter(is_active=True, instrument__is_active=True, instrument__asset_class="CRYPTO")
                .values_list("instrument__symbol", flat=True)
            )
        if action == "clear_unsupported_crypto":
            cleared = clear_unsupported_crypto_symbols(symbols=symbols)
            for tf in ("1d", "5m", "1m"):
                cache.delete(f"freshness:{request.user.pk}:{watchlist.pk if watchlist else 0}:{tf}")
            messages.success(request, f"Cleared {cleared} unsupported-crypto flag(s) for active watchlist symbols.")
            return redirect(f"{request.path}?{urlencode({'timeframe': timeframe})}")
        if action == "clear_provider_cooldowns":
            cleared = clear_provider_cooldowns(symbols=symbols)
            for tf in ("1d", "5m", "1m"):
                cache.delete(f"freshness:{request.user.pk}:{watchlist.pk if watchlist else 0}:{tf}")
            messages.success(request, f"Cleared {cleared} provider cooldown flag(s) for active watchlist symbols.")
            return redirect(f"{request.path}?{urlencode({'timeframe': timeframe})}")
        if action == "run_targeted_crypto_ingest":
            if not watchlist:
                messages.error(request, "No active watchlist found.")
                return redirect(f"{request.path}?{urlencode({'timeframe': timeframe})}")
            max_symbols_raw = (request.POST.get("max_symbols") or "8").strip()
            throttle_raw = (request.POST.get("throttle_seconds") or "1").strip()
            try:
                max_symbols = max(1, min(int(max_symbols_raw), 30))
            except ValueError:
                max_symbols = 8
            try:
                throttle_seconds = max(0.0, min(float(throttle_raw), 5.0))
            except ValueError:
                throttle_seconds = 1.0
            try:
                job = enqueue_watchlist_ingest_job(
                    user=request.user,
                    watchlist_name=watchlist.name,
                    source=IngestionJob.Source.DATA_FRESHNESS,
                    asset_class="CRYPTO",
                    crypto_timeframe=timeframe,
                    stock_timeframe=timeframe,
                    max_symbols=max_symbols,
                    throttle_seconds=throttle_seconds,
                )
                messages.success(
                    request,
                    f"Queued crypto ingest job #{job.id} (max_symbols={max_symbols}, throttle={throttle_seconds}s).",
                )
            except Exception as exc:  # noqa: BLE001
                messages.error(request, f"Unable to queue targeted crypto ingest: {exc}")
            return redirect(f"{request.path}?{urlencode({'timeframe': timeframe})}")
    cache_key = f"freshness:{request.user.pk}:{watchlist.pk if watchlist else 0}:{timeframe}"
    summary = cache.get(cache_key)
    if summary is None:
        summary = build_data_freshness_summary(watchlist=watchlist, timeframe=timeframe, top_n=40)
        cache.set(cache_key, summary, 90)
    recent_jobs = list(IngestionJob.objects.filter(user=request.user).order_by("-created_at")[:8])
    pending_jobs_count = IngestionJob.objects.filter(user=request.user, status=IngestionJob.Status.PENDING).count()
    return render(
        request,
        "dashboard/data_freshness.html",
        {
            "summary": summary,
            "timeframe": timeframe,
            "timeframe_choices": ["1d", "5m", "1m"],
            "recent_jobs": recent_jobs,
            "pending_jobs_count": pending_jobs_count,
        },
    )


@login_required
def symbol_search(request):
    q = (request.GET.get("q") or "").strip().upper()
    instruments = []
    if q:
        instruments = list(
            Instrument.objects.filter(symbol__iexact=q).order_by("symbol")
        )
        if not instruments:
            # Partial match fallback (prefix search)
            instruments = list(
                Instrument.objects.filter(symbol__istartswith=q, is_active=True).order_by("symbol")[:10]
            )

    watchlist = ensure_active_watchlist(request.user)
    watchlist_symbol_set = set()
    if watchlist:
        watchlist_symbol_set = set(
            watchlist.selections.filter(is_active=True)
            .values_list("instrument__symbol", flat=True)
        )

    held_symbol_set = set(
        HeldPosition.objects.filter(user=request.user, status=HeldPosition.Status.OPEN)
        .values_list("instrument__symbol", flat=True)
    )

    results = []
    for instrument in instruments:
        latest_bar = (
            PriceBar.objects.filter(instrument=instrument, timeframe="1d")
            .order_by("-ts")
            .first()
        )
        results.append({
            "instrument": instrument,
            "latest_bar": latest_bar,
            "in_watchlist": instrument.symbol in watchlist_symbol_set,
            "is_held": instrument.symbol in held_symbol_set,
        })

    return render(request, "dashboard/symbol_search.html", {
        "q": q,
        "results": results,
        "watchlist": watchlist,
    })
