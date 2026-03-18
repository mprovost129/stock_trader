from collections import defaultdict

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Avg, Count, Q
from django.shortcuts import render
from django.utils.http import urlencode

from apps.journal.models import JournalEntry
from apps.portfolios.models import HeldPosition, SavedFilterPreset, UserRiskProfile
from apps.portfolios.watchlists import ensure_active_watchlist
from apps.portfolios.services import assess_signal_guardrails, build_holding_health_snapshot, build_signal_correlation_context, summarize_account_drawdown_monitoring, summarize_account_exposure_heatmap, summarize_account_holding_queues, summarize_account_retention_override_posture, summarize_account_retention_template_drift, summarize_account_risk_posture, summarize_account_stop_guardrails, summarize_broker_snapshot_posture, summarize_evidence_lifecycle_automation, summarize_holding_performance, summarize_holding_risk_guardrails, summarize_holding_sector_exposure, summarize_open_holdings, summarize_portfolio_exposure, summarize_portfolio_health_history, summarize_portfolio_health_score, summarize_stop_discipline_history, summarize_stop_discipline_trends, summarize_stop_policy_exception_trends, summarize_stop_policy_timeliness, summarize_watchlist_sectors
from apps.marketdata.models import PriceBar
from apps.signals.models import AlertDelivery, OperatorNotification, PaperTrade, PositionAlert, Signal, SignalOutcome
from apps.signals.services.alerts import build_alert_queue_preview, build_next_session_queue, build_tuning_preview, get_enabled_delivery_channels
from apps.signals.services.delivery_health import get_delivery_health_summary
from apps.signals.services.lifecycle import get_trade_lifecycle_summary
from apps.strategies.models import StrategyRunConfig
from apps.signals.services.position_monitor import rank_open_positions
from apps.signals.views import _extract_signal_filter_params
from apps.portfolios.views import _extract_holding_filter_params




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
    presets = SavedFilterPreset.objects.filter(
        user=user,
        scope=SavedFilterPreset.Scope.SIGNALS,
        is_dashboard_widget=True,
    ).order_by("name")
    for preset in presets:
        filters = _extract_signal_filter_params(preset.filters or {})
        qs = Signal.objects.filter().exclude(direction=Signal.Direction.FLAT)
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
        held_ids = list(HeldPosition.objects.filter(user=user, status=HeldPosition.Status.OPEN).values_list("instrument_id", flat=True).distinct())
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
    signals = (
        Signal.objects.select_related("instrument", "strategy")
        .order_by("-generated_at")
        .all()[:25]
    )
    top_opportunities = list(
        Signal.objects.select_related("instrument", "strategy")
        .exclude(direction=Signal.Direction.FLAT)
        .filter(status__in=[Signal.Status.NEW, Signal.Status.REVIEWED, Signal.Status.TAKEN])
        .order_by("-score", "-generated_at")[:8]
    )
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

    signal_counts = Signal.objects.aggregate(
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
    latest_alerts = list(
        AlertDelivery.objects.select_related("signal", "signal__instrument")
        .order_by("-created_at")[:5]
    )
    recent_failed_alerts = list(
        AlertDelivery.objects.select_related("signal", "signal__instrument")
        .filter(status=AlertDelivery.Status.FAILED)
        .order_by("-created_at")[:5]
    )
    recent_operator_notifications = list(
        OperatorNotification.objects.order_by("-created_at")[:8]
    )
    latest_delivery_escalation = (
        OperatorNotification.objects.filter(kind=OperatorNotification.Kind.DELIVERY_HEALTH, status=OperatorNotification.Status.SENT)
        .order_by("-created_at")
        .first()
    )
    latest_delivery_recovery = (
        OperatorNotification.objects.filter(kind=OperatorNotification.Kind.DELIVERY_RECOVERY, status=OperatorNotification.Status.SENT)
        .order_by("-created_at")
        .first()
    )
    latest_portfolio_health_notification = (
        OperatorNotification.objects.filter(kind=OperatorNotification.Kind.PORTFOLIO_HEALTH, status=OperatorNotification.Status.SENT)
        .order_by("-created_at")
        .first()
    )
    delivery_incident_open = bool(latest_delivery_escalation and (not latest_delivery_recovery or latest_delivery_escalation.created_at > latest_delivery_recovery.created_at))
    delivery_health = get_delivery_health_summary()
    delivery_channels = {
        "enabled": get_enabled_delivery_channels(),
        "discord_enabled": "DISCORD" in get_enabled_delivery_channels(),
        "email_enabled": "EMAIL" in get_enabled_delivery_channels(),
        "email_to": getattr(settings, "ALERT_EMAIL_TO", "").strip(),
        "escalation_cooldown_minutes": int(getattr(settings, "ALERT_ESCALATION_COOLDOWN_MINUTES", 180) or 180),
        "recovery_cooldown_minutes": int(getattr(settings, "ALERT_RECOVERY_COOLDOWN_MINUTES", 60) or 60),
        "latest_delivery_escalation": latest_delivery_escalation,
        "latest_delivery_recovery": latest_delivery_recovery,
        "delivery_incident_open": delivery_incident_open,
    }
    recent_outcomes = list(
        SignalOutcome.objects.select_related("signal", "signal__instrument")
        .exclude(status=SignalOutcome.Status.PENDING)
        .order_by("-updated_at")[:5]
    )
    review_queue = list(
        Signal.objects.select_related("instrument", "strategy")
        .filter(status=Signal.Status.NEW)
        .exclude(direction=Signal.Direction.FLAT)
        .order_by("-score", "-generated_at")[:10]
    )
    pending_outcome_count = Signal.objects.filter(status=Signal.Status.NEW).exclude(outcome__status=SignalOutcome.Status.EVALUATED).count()

    open_positions = list(
        PaperTrade.objects.select_related("signal", "signal__instrument")
        .filter(status=PaperTrade.Status.OPEN)
        .order_by("-updated_at")[:10]
    )
    recent_position_alerts = list(
        PositionAlert.objects.select_related("paper_trade", "paper_trade__signal", "paper_trade__signal__instrument")
        .order_by("-created_at")[:10]
    )
    trade_stats = PaperTrade.objects.filter(opened_by=request.user).aggregate(
        total=Count("id"),
        open_count=Count("id", filter=Q(status=PaperTrade.Status.OPEN)),
        closed_count=Count("id", filter=Q(status=PaperTrade.Status.CLOSED)),
        avg_pnl_pct=Avg("pnl_pct", filter=Q(status=PaperTrade.Status.CLOSED)),
        wins=Count("id", filter=Q(status=PaperTrade.Status.CLOSED, pnl_amount__gt=0)),
        losses=Count("id", filter=Q(status=PaperTrade.Status.CLOSED, pnl_amount__lt=0)),
    )

    alert_policy = {
        "event_threshold": float(getattr(settings, "ALERT_MIN_SCORE_EVENT", 80) or 80),
        "state_threshold": float(getattr(settings, "ALERT_MIN_SCORE_STATE", 60) or 60),
        "state_change_only": bool(getattr(settings, "ALERT_STATE_CHANGE_ONLY", True)),
        "max_age_minutes": int(getattr(settings, "ALERT_MAX_SIGNAL_AGE_MINUTES", 4320) or 4320),
        "cooldown_minutes": int(getattr(settings, "ALERT_COOLDOWN_MINUTES", 30) or 30),
        "max_per_day": int(getattr(settings, "ALERT_MAX_PER_DAY", 12) or 12),
    }
    if alert_policy["event_threshold"] <= 1:
        alert_policy["event_threshold"] *= 100
    if alert_policy["state_threshold"] <= 1:
        alert_policy["state_threshold"] *= 100
    tuning_preview = build_tuning_preview(username=request.user.username, limit=8)
    alert_queue_preview = build_alert_queue_preview(username=request.user.username, limit=10)
    next_session_queue = build_next_session_queue(username=request.user.username, limit=10)
    high_risk_positions = rank_open_positions(username=request.user.username, limit=5)
    trade_lifecycle = get_trade_lifecycle_summary()
    uid = request.user.pk
    _cache_ttl = 60  # seconds — scheduler runs every 5 min; 60s keeps UI snappy without staleness

    def _cached(key, fn):
        result = cache.get(key)
        if result is None:
            result = fn()
            cache.set(key, result, _cache_ttl)
        return result

    held_positions = _cached(f"dash:held_positions:{uid}", lambda: summarize_open_holdings(user=request.user))
    portfolio_exposure = _cached(f"dash:portfolio_exposure:{uid}", lambda: summarize_portfolio_exposure(user=request.user))
    holding_sector_exposure = _cached(f"dash:holding_sector_exposure:{uid}", lambda: summarize_holding_sector_exposure(user=request.user))
    broker_snapshot_posture = _cached(f"dash:broker_snapshot_posture:{uid}", lambda: summarize_broker_snapshot_posture(user=request.user))
    account_risk_posture = _cached(f"dash:account_risk_posture:{uid}", lambda: summarize_account_risk_posture(user=request.user))
    account_exposure_heatmap = _cached(f"dash:account_exposure_heatmap:{uid}", lambda: summarize_account_exposure_heatmap(user=request.user))
    account_drawdown_monitoring = _cached(f"dash:account_drawdown_monitoring:{uid}", lambda: summarize_account_drawdown_monitoring(user=request.user))
    holding_risk_guardrails = _cached(f"dash:holding_risk_guardrails:{uid}", lambda: summarize_holding_risk_guardrails(user=request.user))
    account_stop_guardrails = _cached(f"dash:account_stop_guardrails:{uid}", lambda: summarize_account_stop_guardrails(user=request.user))
    account_holding_queues = _cached(f"dash:account_holding_queues:{uid}", lambda: summarize_account_holding_queues(user=request.user))
    stop_discipline_history = _cached(f"dash:stop_discipline_history:{uid}", lambda: summarize_stop_discipline_history(user=request.user))
    stop_discipline_trends = _cached(f"dash:stop_discipline_trends:{uid}", lambda: summarize_stop_discipline_trends(user=request.user))
    stop_policy_timeliness = _cached(f"dash:stop_policy_timeliness:{uid}", lambda: summarize_stop_policy_timeliness(user=request.user))
    stop_policy_exception_trends = _cached(f"dash:stop_policy_exception_trends:{uid}", lambda: summarize_stop_policy_exception_trends(user=request.user))
    account_retention_override_posture = _cached(f"dash:account_retention_override_posture:{uid}", lambda: summarize_account_retention_override_posture(user=request.user))
    account_retention_template_drift = _cached(f"dash:account_retention_template_drift:{uid}", lambda: summarize_account_retention_template_drift(user=request.user))
    evidence_lifecycle_automation = _cached(f"dash:evidence_lifecycle_automation:{uid}", lambda: summarize_evidence_lifecycle_automation(user=request.user))
    portfolio_health = _cached(f"dash:portfolio_health:{uid}", lambda: summarize_portfolio_health_score(user=request.user))
    portfolio_health_history = _cached(f"dash:portfolio_health_history:{uid}", lambda: summarize_portfolio_health_history(user=request.user, limit=6))
    risk_profile = UserRiskProfile.objects.filter(user=request.user).first()
    correlation_context = build_signal_correlation_context(user=request.user, risk_profile=risk_profile)
    top_opportunity_guardrails = {}
    top_opportunity_guardrail_summary = {"OK": 0, "NEAR": 0, "OVER": 0, "NO_PROFILE": 0, "NO_PLAN": 0}
    for signal in top_opportunities:
        plan = getattr(signal, "trade_plan", None)
        suggested_qty = getattr(plan, "suggested_qty", None) if plan else None
        display_price = getattr(signal, "display_price", None)
        guardrails = assess_signal_guardrails(
            user=request.user,
            signal=signal,
            entry_price=display_price,
            suggested_qty=suggested_qty,
            portfolio_exposure=portfolio_exposure,
            sector_exposure=holding_sector_exposure,
            correlation_context=correlation_context,
        )
        top_opportunity_guardrails[signal.pk] = guardrails
        top_opportunity_guardrail_summary[guardrails["overall_posture"]] = top_opportunity_guardrail_summary.get(guardrails["overall_posture"], 0) + 1
    top_opportunity_guardrail_summary["MISSING"] = top_opportunity_guardrail_summary.get("NO_PROFILE", 0) + top_opportunity_guardrail_summary.get("NO_PLAN", 0)
    signal_preset_widgets = _build_signal_preset_widgets(request.user)
    holding_preset_widgets = _build_holding_preset_widgets(request.user)
    analytics_summary = _build_trade_analytics(user=request.user)["summary"]

    setup_items = {
        "watchlist_ready": watchlist_count > 0,
        "strategy_ready": active_configs > 0,
        "signals_ready": signal_counts["total"] > 0,
        "journal_ready": journal_counts["total"] > 0,
    }

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
        "latest_alerts": latest_alerts,
        "recent_failed_alerts": recent_failed_alerts,
        "delivery_channels": delivery_channels,
        "delivery_health": delivery_health,
        "recent_operator_notifications": recent_operator_notifications,
        "setup_items": setup_items,
        "top_opportunities": top_opportunities,
        "review_queue": review_queue,
        "recent_outcomes": recent_outcomes,
        "pending_outcome_count": pending_outcome_count,
        "alert_policy": alert_policy,
        "tuning_preview": tuning_preview,
        "open_positions": open_positions,
        "recent_position_alerts": recent_position_alerts,
        "trade_stats": trade_stats,
        "alert_queue_preview": alert_queue_preview,
        "next_session_queue": next_session_queue,
        "high_risk_positions": high_risk_positions,
        "trade_lifecycle": trade_lifecycle,
        "held_positions": held_positions,
        "signal_preset_widgets": signal_preset_widgets,
        "holding_preset_widgets": holding_preset_widgets,
        "portfolio_exposure": portfolio_exposure,
        "holding_sector_exposure": holding_sector_exposure,
        "broker_snapshot_posture": broker_snapshot_posture,
        "account_risk_posture": account_risk_posture,
        "account_exposure_heatmap": account_exposure_heatmap,
        "account_drawdown_monitoring": account_drawdown_monitoring,
        "holding_risk_guardrails": holding_risk_guardrails,
        "account_stop_guardrails": account_stop_guardrails,
        "account_holding_queues": account_holding_queues,
        "stop_discipline_history": stop_discipline_history,
        "stop_discipline_trends": stop_discipline_trends,
        "stop_policy_timeliness": stop_policy_timeliness,
        "stop_policy_exception_trends": stop_policy_exception_trends,
        "account_retention_override_posture": account_retention_override_posture,
        "account_retention_template_drift": account_retention_template_drift,
        "evidence_lifecycle_automation": evidence_lifecycle_automation,
        "portfolio_health": portfolio_health,
        "portfolio_health_history": portfolio_health_history,
        "latest_portfolio_health_notification": latest_portfolio_health_notification,
        "risk_profile": risk_profile,
        "top_opportunity_guardrails": top_opportunity_guardrails,
        "top_opportunity_guardrail_summary": top_opportunity_guardrail_summary,
        "analytics_summary": analytics_summary,
    }
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
    timeframe_choices = list(Signal.objects.order_by().values_list("timeframe", flat=True).distinct())
    strategy_choices = list(Signal.objects.order_by().values_list("strategy__slug", flat=True).distinct())
    return render(request, "dashboard/analytics.html", {
        "analytics": analytics_data,
        "timeframe": timeframe,
        "strategy": strategy,
        "min_count": min_count,
        "timeframe_choices": timeframe_choices,
        "strategy_choices": strategy_choices,
    })

