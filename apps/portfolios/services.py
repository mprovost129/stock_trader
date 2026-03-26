from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from math import sqrt

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.marketdata.models import Instrument, PriceBar
from apps.signals.models import Signal
from apps.signals.services.alerts import _post_discord, get_enabled_delivery_channels

from .models import AccountRetentionPolicyOverride, AccountRetentionPolicyTemplate, BrokerPositionImportResolution, BrokerPositionImportRun, EvidenceLifecycleAutomationRun, HeldPosition, HoldingAlert, HoldingTransaction, PortfolioHealthSnapshot
from .models import ImportedBrokerSnapshot, InstrumentSelection, UserRiskProfile


RETENTION_POLICY_FIELDS = (
    ("evidence_retention_default_days", "Default"),
    ("evidence_retention_verified_days", "Verified"),
    ("evidence_retention_strong_days", "Strong"),
    ("evidence_retention_weak_days", "Weak"),
    ("evidence_retention_placeholder_days", "Placeholder"),
    ("evidence_retention_confirmation_days", "Confirmation"),
    ("evidence_retention_import_match_days", "Import match"),
)


def _normalize_watchlist_sector_label(value: str | None) -> str:
    value = (value or "").strip()
    return value or "Uncategorized"


def _normalize_signal_score(score) -> float | None:
    if score is None:
        return None
    value = float(score)
    if value <= 1:
        value *= 100
    return max(0.0, min(100.0, value))


def _build_daily_return_series(*, instrument_ids: list[int], lookback_bars: int) -> dict[int, list[float]]:
    if not instrument_ids:
        return {}
    lookback_bars = max(10, int(lookback_bars or 60))
    rows = (
        PriceBar.objects.filter(instrument_id__in=instrument_ids, timeframe=PriceBar.Timeframe.D1)
        .order_by("instrument_id", "-ts")
        .values_list("instrument_id", "close")
    )
    closes_map: dict[int, list[Decimal]] = {instrument_id: [] for instrument_id in instrument_ids}
    for instrument_id, close in rows:
        bucket = closes_map.setdefault(instrument_id, [])
        if len(bucket) >= lookback_bars + 1:
            continue
        bucket.append(Decimal(close))

    out: dict[int, list[float]] = {}
    for instrument_id, closes in closes_map.items():
        if len(closes) < 3:
            continue
        closes = list(reversed(closes))
        returns: list[float] = []
        previous = closes[0]
        for current in closes[1:]:
            if previous <= 0:
                previous = current
                continue
            returns.append(float((current / previous) - Decimal("1")))
            previous = current
        if len(returns) >= 2:
            out[instrument_id] = returns
    return out


def _pearson_correlation(left: list[float], right: list[float]) -> float | None:
    n = min(len(left), len(right))
    if n < 3:
        return None
    left = left[-n:]
    right = right[-n:]
    mean_left = sum(left) / n
    mean_right = sum(right) / n
    cov = sum((lx - mean_left) * (rx - mean_right) for lx, rx in zip(left, right))
    var_left = sum((lx - mean_left) ** 2 for lx in left)
    var_right = sum((rx - mean_right) ** 2 for rx in right)
    if var_left <= 0 or var_right <= 0:
        return None
    return cov / sqrt(var_left * var_right)


def build_signal_correlation_context(*, user, risk_profile=None) -> dict:
    if risk_profile is None:
        try:
            from .models import UserRiskProfile
            risk_profile = UserRiskProfile.objects.filter(user=user).first()
        except Exception:
            risk_profile = None

    held_positions = list(
        HeldPosition.objects.select_related("instrument")
        .filter(user=user, status=HeldPosition.Status.OPEN)
        .order_by("instrument__symbol", "id")
    )
    lookback_bars = int(getattr(risk_profile, "correlation_lookback_bars", 60) or 60)
    threshold = Decimal(getattr(risk_profile, "high_correlation_threshold", Decimal("0.80")) or Decimal("0.80"))
    max_high_corr = int(getattr(risk_profile, "max_high_correlation_positions", 2) or 2)
    instrument_ids = [position.instrument_id for position in held_positions]
    return_series = _build_daily_return_series(instrument_ids=instrument_ids, lookback_bars=lookback_bars)
    return {
        "held_positions": held_positions,
        "return_series": return_series,
        "lookback_bars": lookback_bars,
        "high_correlation_threshold": threshold,
        "max_high_correlation_positions": max_high_corr,
    }


def evaluate_signal_correlation_risk(*, signal_instrument_id: int, correlation_context: dict | None) -> dict:
    if not correlation_context:
        return {
            "correlation_posture": None,
            "high_correlation_count": None,
            "max_high_correlation_positions": None,
            "high_correlation_threshold": None,
            "high_correlation_symbols": [],
            "lookback_bars": None,
            "reason": None,
        }

    held_positions = correlation_context.get("held_positions") or []
    if not held_positions:
        return {
            "correlation_posture": "OK",
            "high_correlation_count": 0,
            "max_high_correlation_positions": correlation_context.get("max_high_correlation_positions"),
            "high_correlation_threshold": correlation_context.get("high_correlation_threshold"),
            "high_correlation_symbols": [],
            "lookback_bars": correlation_context.get("lookback_bars"),
            "reason": "No open holdings to compare against.",
        }

    return_series = correlation_context.get("return_series") or {}
    candidate_series = return_series.get(signal_instrument_id)
    if candidate_series is None:
        candidate_series = _build_daily_return_series(
            instrument_ids=[signal_instrument_id],
            lookback_bars=correlation_context.get("lookback_bars") or 60,
        ).get(signal_instrument_id)
    if not candidate_series:
        return {
            "correlation_posture": None,
            "high_correlation_count": None,
            "max_high_correlation_positions": correlation_context.get("max_high_correlation_positions"),
            "high_correlation_threshold": correlation_context.get("high_correlation_threshold"),
            "high_correlation_symbols": [],
            "lookback_bars": correlation_context.get("lookback_bars"),
            "reason": "Not enough daily history to score recent return correlation.",
        }

    threshold = float(correlation_context.get("high_correlation_threshold") or Decimal("0.80"))
    max_high_corr = max(1, int(correlation_context.get("max_high_correlation_positions") or 1))
    matches = []
    seen_instrument_ids: set[int] = set()
    for position in held_positions:
        if position.instrument_id == signal_instrument_id or position.instrument_id in seen_instrument_ids:
            continue
        seen_instrument_ids.add(position.instrument_id)
        held_series = return_series.get(position.instrument_id)
        if not held_series:
            continue
        corr = _pearson_correlation(candidate_series, held_series)
        if corr is None:
            continue
        if corr >= threshold:
            matches.append({
                "symbol": position.instrument.symbol,
                "correlation": round(corr, 2),
            })

    matches.sort(key=lambda item: (-item["correlation"], item["symbol"]))
    high_correlation_count = len(matches)
    if high_correlation_count >= max_high_corr:
        posture = "OVER"
        reason = f"Recent returns are highly correlated with {high_correlation_count} currently held names."
    elif high_correlation_count == max(0, max_high_corr - 1) and high_correlation_count > 0:
        posture = "NEAR"
        reason = "This setup would sit one step away from your correlation cluster limit."
    else:
        posture = "OK"
        reason = "Recent return correlation does not currently create a crowded cluster."
    return {
        "correlation_posture": posture,
        "high_correlation_count": high_correlation_count,
        "max_high_correlation_positions": max_high_corr,
        "high_correlation_threshold": correlation_context.get("high_correlation_threshold"),
        "high_correlation_symbols": matches[:3],
        "lookback_bars": correlation_context.get("lookback_bars"),
        "reason": reason,
    }


def summarize_watchlist_sectors(*, watchlist, user=None, limit: int | None = None) -> list[dict]:
    if watchlist is None:
        return []
    selections = list(
        InstrumentSelection.objects.select_related("instrument")
        .filter(watchlist=watchlist, is_active=True, instrument__is_active=True)
        .order_by("instrument__symbol")
    )
    if not selections:
        return []

    instrument_ids = [item.instrument_id for item in selections]
    held_ids = set()
    if user is not None:
        held_ids = set(
            HeldPosition.objects.filter(user=user, status=HeldPosition.Status.OPEN, instrument_id__in=instrument_ids)
            .values_list("instrument_id", flat=True)
        )

    recent_signal_map: dict[int, Signal] = {}
    recent_signals = (
        Signal.objects.select_related("instrument", "strategy")
        .filter(instrument_id__in=instrument_ids)
        .exclude(direction=Signal.Direction.FLAT)
        .order_by("instrument_id", "-generated_at", "-id")
    )
    seen = set()
    for sig in recent_signals:
        if sig.instrument_id in seen:
            continue
        recent_signal_map[sig.instrument_id] = sig
        seen.add(sig.instrument_id)
        if len(seen) == len(instrument_ids):
            break

    buckets: dict[str, dict] = {}
    for selection in selections:
        label = _normalize_watchlist_sector_label(selection.sector)
        bucket = buckets.setdefault(label, {
            "label": label,
            "symbol_count": 0,
            "held_count": 0,
            "long_count": 0,
            "short_count": 0,
            "score_seed": [],
            "leaders_seed": [],
        })
        bucket["symbol_count"] += 1
        if selection.instrument_id in held_ids:
            bucket["held_count"] += 1
        recent = recent_signal_map.get(selection.instrument_id)
        if recent:
            if recent.direction == Signal.Direction.LONG:
                bucket["long_count"] += 1
            elif recent.direction == Signal.Direction.SHORT:
                bucket["short_count"] += 1
            score_value = _normalize_signal_score(getattr(recent, "score", None))
            if score_value is not None:
                bucket["score_seed"].append(score_value)
                bucket["leaders_seed"].append((score_value, selection.instrument.symbol))

    summaries: list[dict] = []
    for label, bucket in buckets.items():
        avg_score = round(sum(bucket["score_seed"]) / len(bucket["score_seed"]), 1) if bucket["score_seed"] else None
        long_count = bucket["long_count"]
        short_count = bucket["short_count"]
        if long_count > short_count and (avg_score or 0) >= 60:
            posture = "Leading"
        elif short_count > long_count:
            posture = "Weakening"
        else:
            posture = "Mixed"
        leaders = [symbol for _score, symbol in sorted(bucket["leaders_seed"], key=lambda item: (-item[0], item[1]))[:3]]
        summaries.append({
            "label": label,
            "symbol_count": bucket["symbol_count"],
            "held_count": bucket["held_count"],
            "long_count": long_count,
            "short_count": short_count,
            "avg_score": avg_score,
            "posture": posture,
            "leaders": leaders,
        })

    summaries.sort(key=lambda item: (-item["symbol_count"], item["label"].lower()))
    if limit:
        summaries = summaries[:limit]
    return summaries


def build_active_watchlist_sector_map(*, user) -> dict[int, str]:
    try:
        from .watchlists import ensure_active_watchlist
        watchlist = ensure_active_watchlist(user)
    except Exception:
        return {}
    return {
        selection.instrument_id: _normalize_watchlist_sector_label(selection.sector)
        for selection in InstrumentSelection.objects.filter(watchlist=watchlist, is_active=True)
    }


def assess_signal_guardrails(*, user, signal, entry_price, suggested_qty, portfolio_exposure=None, sector_exposure=None, correlation_context=None) -> dict:
    """Assess whether a candidate signal still fits cash, concentration, and correlation guardrails."""
    if entry_price is None or not suggested_qty:
        return {
            "overall_posture": "NO_PLAN",
            "overall_label": "no plan",
            "overall_reason": "No trade-plan sizing is available for this signal yet.",
            "fits_headroom": None,
            "position_posture": None,
            "sector_posture": None,
            "projected_position_weight_pct": None,
            "projected_sector_weight_pct": None,
            "current_position_weight_pct": None,
            "current_sector_weight_pct": None,
            "projected_cash_headroom": None,
            "sector_label": None,
            "correlation_posture": None,
            "high_correlation_count": None,
            "max_high_correlation_positions": None,
            "high_correlation_threshold": None,
            "high_correlation_symbols": [],
            "correlation_lookback_bars": None,
            "projected_net_exposure_pct": None,
            "current_net_exposure_pct": None,
            "net_exposure_posture": None,
            "net_exposure_headroom_pct": None,
            "max_net_exposure_pct": None,
            "messages": ["No suggested trade size available yet."],
        }

    portfolio_exposure = portfolio_exposure or summarize_portfolio_exposure(user=user)
    sector_exposure = sector_exposure or summarize_holding_sector_exposure(user=user)

    try:
        from .models import UserRiskProfile
        risk_profile = UserRiskProfile.objects.filter(user=user).first()
    except Exception:
        risk_profile = None

    if risk_profile is None or not risk_profile.account_equity:
        return {
            "overall_posture": "NO_PROFILE",
            "overall_label": "no profile",
            "overall_reason": "Set account equity in Allocation Controls to evaluate guardrails.",
            "fits_headroom": None,
            "position_posture": None,
            "sector_posture": None,
            "projected_position_weight_pct": None,
            "projected_sector_weight_pct": None,
            "current_position_weight_pct": None,
            "current_sector_weight_pct": None,
            "projected_cash_headroom": None,
            "sector_label": None,
            "correlation_posture": None,
            "high_correlation_count": None,
            "max_high_correlation_positions": None,
            "high_correlation_threshold": None,
            "high_correlation_symbols": [],
            "correlation_lookback_bars": None,
            "projected_net_exposure_pct": None,
            "current_net_exposure_pct": None,
            "net_exposure_posture": None,
            "net_exposure_headroom_pct": None,
            "max_net_exposure_pct": None,
            "messages": ["Account equity is not configured yet."],
        }

    account_equity = Decimal(risk_profile.account_equity)
    if account_equity <= 0:
        return {
            "overall_posture": "NO_PROFILE",
            "overall_label": "no profile",
            "overall_reason": "Account equity must be greater than zero to evaluate guardrails.",
            "fits_headroom": None,
            "position_posture": None,
            "sector_posture": None,
            "projected_position_weight_pct": None,
            "projected_sector_weight_pct": None,
            "current_position_weight_pct": None,
            "current_sector_weight_pct": None,
            "projected_cash_headroom": None,
            "sector_label": None,
            "correlation_posture": None,
            "high_correlation_count": None,
            "max_high_correlation_positions": None,
            "high_correlation_threshold": None,
            "high_correlation_symbols": [],
            "correlation_lookback_bars": None,
            "projected_net_exposure_pct": None,
            "current_net_exposure_pct": None,
            "net_exposure_posture": None,
            "net_exposure_headroom_pct": None,
            "max_net_exposure_pct": None,
            "messages": ["Account equity must be greater than zero."],
        }

    suggested_cost = (Decimal(entry_price) * Decimal(suggested_qty)).quantize(Decimal("0.01"))
    cash_headroom = portfolio_exposure.get("cash_headroom")
    fits_headroom = None if cash_headroom is None else suggested_cost <= cash_headroom
    projected_cash_headroom = None if cash_headroom is None else (Decimal(cash_headroom) - suggested_cost).quantize(Decimal("0.01"))

    warning_buffer = Decimal(risk_profile.concentration_warning_buffer_pct or 0)
    max_position_weight_pct = Decimal(risk_profile.max_position_weight_pct or 0)
    max_sector_weight_pct = Decimal(risk_profile.max_sector_weight_pct or 0)
    max_net_exposure_pct = Decimal(risk_profile.max_net_exposure_pct or 0)
    net_exposure_warning_buffer_pct = Decimal(risk_profile.net_exposure_warning_buffer_pct or 0)

    open_positions = HeldPosition.objects.filter(user=user, status=HeldPosition.Status.OPEN, instrument_id=signal.instrument_id)
    existing_position_value = Decimal("0.00")
    for held in open_positions:
        price_used = Decimal(held.last_price) if held.last_price is not None else Decimal(held.average_entry_price)
        existing_position_value += (Decimal(held.quantity) * price_used).quantize(Decimal("0.01"))
    projected_position_value = (existing_position_value + suggested_cost).quantize(Decimal("0.01"))
    current_position_weight_pct = ((existing_position_value / account_equity) * Decimal("100")).quantize(Decimal("0.01")) if existing_position_value > 0 else Decimal("0.00")
    projected_position_weight_pct = ((projected_position_value / account_equity) * Decimal("100")).quantize(Decimal("0.01"))

    sector_map = build_active_watchlist_sector_map(user=user)
    sector_label = sector_map.get(signal.instrument_id, "Unassigned")
    sector_value_map = {row["sector"]: Decimal(row["market_value"]) for row in sector_exposure.get("rows", [])}
    current_sector_value = sector_value_map.get(sector_label, Decimal("0.00"))
    projected_sector_value = (current_sector_value + suggested_cost).quantize(Decimal("0.01"))
    current_sector_weight_pct = ((current_sector_value / account_equity) * Decimal("100")).quantize(Decimal("0.01")) if current_sector_value > 0 else Decimal("0.00")
    projected_sector_weight_pct = ((projected_sector_value / account_equity) * Decimal("100")).quantize(Decimal("0.01"))

    current_net_exposure_pct = Decimal(portfolio_exposure.get("net_exposure_pct") or Decimal("0.00")).quantize(Decimal("0.01"))
    projected_net_exposure_pct = ((Decimal(portfolio_exposure.get("total_market_value") or Decimal("0.00")) + suggested_cost) / account_equity * Decimal("100")).quantize(Decimal("0.01"))

    def posture(projected_pct: Decimal, limit_pct: Decimal, buffer_pct: Decimal | None = None) -> tuple[str | None, Decimal | None]:
        if limit_pct <= 0:
            return None, None
        headroom = (limit_pct - projected_pct).quantize(Decimal("0.01"))
        near_floor = max(Decimal("0.00"), limit_pct - (warning_buffer if buffer_pct is None else buffer_pct))
        if projected_pct > limit_pct:
            return "OVER", headroom
        if projected_pct >= near_floor:
            return "NEAR", headroom
        return "OK", headroom

    position_posture, position_headroom_pct = posture(projected_position_weight_pct, max_position_weight_pct)
    sector_posture, sector_headroom_pct = posture(projected_sector_weight_pct, max_sector_weight_pct)
    net_exposure_posture, net_exposure_headroom_pct = posture(projected_net_exposure_pct, max_net_exposure_pct, net_exposure_warning_buffer_pct)

    correlation_context = correlation_context or build_signal_correlation_context(user=user, risk_profile=risk_profile)
    correlation_risk = evaluate_signal_correlation_risk(signal_instrument_id=signal.instrument_id, correlation_context=correlation_context)
    correlation_posture = correlation_risk["correlation_posture"]

    messages = []
    if fits_headroom is False:
        messages.append("Suggested cost is above remaining cash headroom.")
    elif projected_cash_headroom is not None:
        messages.append(f"Cash headroom after entry: ${projected_cash_headroom}")
    if net_exposure_posture == "OVER":
        messages.append(f"Projected net exposure {projected_net_exposure_pct}% is above the {max_net_exposure_pct}% portfolio net-exposure cap.")
    elif net_exposure_posture == "NEAR":
        messages.append(f"Projected net exposure {projected_net_exposure_pct}% is near the {max_net_exposure_pct}% portfolio net-exposure cap.")
    if position_posture == "OVER":
        messages.append(f"Projected position weight {projected_position_weight_pct}% is above the {max_position_weight_pct}% single-position cap.")
    elif position_posture == "NEAR":
        messages.append(f"Projected position weight {projected_position_weight_pct}% is near the {max_position_weight_pct}% single-position cap.")
    if sector_posture == "OVER":
        messages.append(f"Projected {sector_label} exposure {projected_sector_weight_pct}% is above the {max_sector_weight_pct}% sector cap.")
    elif sector_posture == "NEAR":
        messages.append(f"Projected {sector_label} exposure {projected_sector_weight_pct}% is near the {max_sector_weight_pct}% sector cap.")
    if correlation_posture == "OVER":
        matches = ", ".join(f"{item['symbol']} ({item['correlation']})" for item in correlation_risk["high_correlation_symbols"])
        messages.append(
            f"Recent returns are already highly correlated with {correlation_risk['high_correlation_count']} held names at or above {correlation_risk['high_correlation_threshold']}: {matches or 'cluster detected'}."
        )
    elif correlation_posture == "NEAR":
        matches = ", ".join(f"{item['symbol']} ({item['correlation']})" for item in correlation_risk["high_correlation_symbols"])
        messages.append(
            f"This setup is near your correlation cluster limit ({correlation_risk['high_correlation_count']} held names at or above {correlation_risk['high_correlation_threshold']}). {matches or ''}".strip()
        )

    if fits_headroom is False or net_exposure_posture == "OVER" or position_posture == "OVER" or sector_posture == "OVER" or correlation_posture == "OVER":
        overall_posture = "OVER"
        overall_label = "over guardrails"
        overall_reason = "This trade would push portfolio deployment, concentration, or correlation clustering beyond your current limits."
    elif net_exposure_posture == "NEAR" or position_posture == "NEAR" or sector_posture == "NEAR" or correlation_posture == "NEAR":
        overall_posture = "NEAR"
        overall_label = "near limit"
        overall_reason = "This trade still fits, but it would leave little room under one of your risk caps."
    else:
        overall_posture = "OK"
        overall_label = "fits"
        overall_reason = "This trade fits current cash, deployment, concentration, and correlation guardrails."

    return {
        "overall_posture": overall_posture,
        "overall_label": overall_label,
        "overall_reason": overall_reason,
        "fits_headroom": fits_headroom,
        "net_exposure_posture": net_exposure_posture,
        "net_exposure_headroom_pct": net_exposure_headroom_pct,
        "position_posture": position_posture,
        "position_headroom_pct": position_headroom_pct,
        "sector_posture": sector_posture,
        "sector_headroom_pct": sector_headroom_pct,
        "projected_net_exposure_pct": projected_net_exposure_pct,
        "current_net_exposure_pct": current_net_exposure_pct,
        "max_net_exposure_pct": max_net_exposure_pct,
        "projected_position_weight_pct": projected_position_weight_pct,
        "projected_sector_weight_pct": projected_sector_weight_pct,
        "current_position_weight_pct": current_position_weight_pct,
        "current_sector_weight_pct": current_sector_weight_pct,
        "projected_cash_headroom": projected_cash_headroom,
        "sector_label": sector_label,
        "correlation_posture": correlation_posture,
        "high_correlation_count": correlation_risk["high_correlation_count"],
        "max_high_correlation_positions": correlation_risk["max_high_correlation_positions"],
        "high_correlation_threshold": correlation_risk["high_correlation_threshold"],
        "high_correlation_symbols": correlation_risk["high_correlation_symbols"],
        "correlation_lookback_bars": correlation_risk["lookback_bars"],
        "messages": messages,
    }


@dataclass(frozen=True)
class HoldingHealthSnapshot:
    position: HeldPosition
    current_price: Decimal | None
    pnl_pct: float | None
    status_label: str
    thesis_broken: bool
    stop_breached: bool
    target_reached: bool
    deteriorating: bool
    warning_drawdown: bool
    opposing_signal: Signal | None
    recommendation_code: str
    recommendation_label: str
    recommendation_reason: str
    recommendation_rank: int
    suggested_action_label: str
    suggested_action_quantity: Decimal | None
    suggested_action_pct: Decimal | None
    suggested_action_reason: str
    missing_stop: bool
    stop_too_wide: bool
    near_stop: bool
    stop_loss_pct: Decimal | None
    risk_guardrail_posture: str
    risk_guardrail_label: str
    risk_guardrail_reason: str
    risk_guardrail_action: str


def latest_price_for_position(position: HeldPosition):
    return (
        PriceBar.objects.filter(instrument=position.instrument, timeframe="1d")
        .order_by("-ts")
        .first()
    )


def refresh_position_market_state(position: HeldPosition) -> HeldPosition:
    bar = latest_price_for_position(position)
    if not bar:
        return position
    current = Decimal(bar.close)
    entry = Decimal(position.average_entry_price)
    qty = Decimal(position.quantity)
    pnl_amount = (current - entry) * qty
    pnl_pct = float(((current - entry) / entry) * Decimal("100")) if entry else None

    changed = []
    if position.last_price != current:
        position.last_price = current
        changed.append("last_price")
    if position.last_price_at != bar.ts:
        position.last_price_at = bar.ts
        changed.append("last_price_at")
    pnl_amount = pnl_amount.quantize(Decimal("0.01"))
    if position.pnl_amount != pnl_amount:
        position.pnl_amount = pnl_amount
        changed.append("pnl_amount")
    normalized_pct = round(pnl_pct, 4) if pnl_pct is not None else None
    if position.pnl_pct != normalized_pct:
        position.pnl_pct = normalized_pct
        changed.append("pnl_pct")
    if changed:
        position.save(update_fields=changed + ["updated_at"])
    return position


def _get_position_risk_profile(position: HeldPosition) -> UserRiskProfile | None:
    return UserRiskProfile.objects.filter(user=position.user).first()


def _build_risk_guardrail(*, position: HeldPosition, current: Decimal | None, stop_breached: bool) -> dict:
    profile = _get_position_risk_profile(position)
    require_stop = True if profile is None else bool(profile.require_stop_for_open_positions)
    max_stop_loss_pct = Decimal(str(getattr(profile, "max_stop_loss_pct", Decimal("8")) or Decimal("8")))
    stop_warning_buffer_pct = Decimal(str(getattr(profile, "stop_warning_buffer_pct", Decimal("1.50")) or Decimal("1.50")))
    drawdown_review_pct = Decimal(str(getattr(profile, "drawdown_review_pct", getattr(settings, "HELD_POSITION_REVIEW_WARNING_PCT", 2.5)) or 2.5))
    drawdown_urgent_pct = Decimal(str(getattr(profile, "drawdown_urgent_pct", getattr(settings, "HELD_POSITION_DETERIORATION_ALERT_PCT", 5.0)) or 5.0))

    entry = Decimal(position.average_entry_price or 0)
    stop = Decimal(position.stop_price) if position.stop_price is not None else None
    stop_loss_pct = None
    if entry > 0 and stop is not None:
        stop_loss_pct = (((entry - stop) / entry) * Decimal("100")).quantize(Decimal("0.01"))

    near_stop = False
    if current is not None and stop is not None and current > 0 and current > stop:
        distance_pct = (((current - stop) / current) * Decimal("100")).quantize(Decimal("0.01"))
        near_stop = distance_pct <= abs(stop_warning_buffer_pct)

    missing_stop = position.status == HeldPosition.Status.OPEN and require_stop and stop is None
    stop_too_wide = bool(stop_loss_pct is not None and stop_loss_pct > abs(max_stop_loss_pct))
    pnl_pct = Decimal(str(position.pnl_pct)) if position.pnl_pct is not None else None
    review_drawdown = bool(pnl_pct is not None and pnl_pct <= -abs(drawdown_review_pct))
    urgent_drawdown = bool(pnl_pct is not None and pnl_pct <= -abs(drawdown_urgent_pct))

    posture = "OK"
    label = "within guardrails"
    reason = "This holding has an explicit stop and no immediate stop-loss guardrail pressure."
    action = "No risk-guardrail fix needed."
    if stop_breached:
        posture = "OVER"
        label = "stop violated"
        reason = "Price is already through the recorded stop, so the stop-loss guardrail has failed."
        action = "Exit or reduce the position now instead of leaving it unmanaged."
    elif missing_stop:
        posture = "OVER"
        label = "missing stop"
        reason = "This open holding has no recorded stop even though stop discipline is enabled in Allocation Controls."
        action = "Add an explicit stop or decide the manual exit level before continuing to hold it."
    elif stop_too_wide:
        posture = "OVER"
        label = "stop too wide"
        reason = f"The recorded stop allows about {stop_loss_pct}% downside from entry, which is wider than the configured {max_stop_loss_pct}% guardrail."
        action = "Tighten the stop or reduce position size so the downside plan matches the risk guardrail."
    elif urgent_drawdown:
        posture = "OVER"
        label = "urgent drawdown"
        reason = f"The holding is down {position.pnl_pct}% and has crossed the urgent drawdown guardrail of {drawdown_urgent_pct}%."
        action = "Review the position now and either tighten the stop, cut size, or exit."
    elif near_stop:
        posture = "NEAR"
        label = "near stop"
        reason = f"Current price is within {stop_warning_buffer_pct}% of the stop, so one more move can trigger the exit level."
        action = "Prepare the next action now instead of waiting for a surprise stop breach."
    elif review_drawdown:
        posture = "NEAR"
        label = "review drawdown"
        reason = f"The holding is down {position.pnl_pct}% and has crossed the review drawdown guardrail of {drawdown_review_pct}%."
        action = "Re-check the thesis and decide whether to tighten the stop or trim the position."

    return {
        "missing_stop": missing_stop,
        "stop_too_wide": stop_too_wide,
        "near_stop": near_stop,
        "stop_loss_pct": stop_loss_pct,
        "risk_guardrail_posture": posture,
        "risk_guardrail_label": label,
        "risk_guardrail_reason": reason,
        "risk_guardrail_action": action,
    }


def _build_recommendation(*, stop_breached: bool, thesis_broken: bool, target_reached: bool, deteriorating: bool, warning_drawdown: bool, pnl_pct: float | None) -> tuple[str, str, str, int]:
    sell_on_short_with_loss = bool(getattr(settings, "HELD_POSITION_SELL_ON_SHORT_WITH_LOSS", True))
    if stop_breached:
        return ("SELL_NOW", "sell now", "Price is below your stop. Exit discipline has already been violated.", 100)
    if thesis_broken and deteriorating:
        return ("SELL_NOW", "sell now", "A live SHORT signal appeared and the position is already in deep drawdown.", 95)
    if thesis_broken and sell_on_short_with_loss and pnl_pct is not None and pnl_pct <= 0:
        return ("SELL_NOW", "sell now", "A live SHORT signal appeared after entry and the position is no longer profitable.", 90)
    if target_reached:
        return ("TRIM_OR_EXIT", "trim / exit", "Your target has been reached. Lock gains or reduce size according to plan.", 70)
    if thesis_broken:
        return ("REVIEW_URGENT", "urgent review", "A live SHORT signal appeared after entry. Recheck the thesis before holding further.", 80)
    if deteriorating:
        return ("REVIEW_URGENT", "urgent review", "The position is in deep drawdown versus your configured deterioration threshold.", 75)
    if warning_drawdown:
        return ("REVIEW", "review", "The position is slipping enough to justify a manual re-check before it becomes a deeper loss.", 55)
    return ("HOLD", "hold", "No current sell condition is active. Continue monitoring the position.", 10)






def _build_action_plan(*, position: HeldPosition, recommendation_code: str, pnl_pct: float | None) -> tuple[str, Decimal | None, Decimal | None, str]:
    quantity = Decimal(position.quantity)
    if quantity <= 0:
        return ("hold current size", None, None, "No remaining quantity is recorded on this holding.")
    trim_pct = Decimal(str(getattr(settings, "HELD_POSITION_TARGET_TRIM_PCT", 0.50) or 0.50))
    urgent_trim_pct = Decimal(str(getattr(settings, "HELD_POSITION_URGENT_REDUCE_PCT", 0.25) or 0.25))
    review_trim_pct = Decimal(str(getattr(settings, "HELD_POSITION_REVIEW_REDUCE_PCT", 0.10) or 0.10))

    def _qty_for_pct(pct: Decimal) -> Decimal:
        raw = (quantity * pct).quantize(Decimal("0.00000001"))
        return raw if raw > 0 else quantity

    if recommendation_code == "SELL_NOW":
        return ("sell full position", quantity, Decimal("100.00"), "Exit the entire remaining position because the sell condition is already active.")
    if recommendation_code == "TRIM_OR_EXIT":
        qty = _qty_for_pct(trim_pct)
        pct = (trim_pct * Decimal("100")).quantize(Decimal("0.01"))
        return ("trim gains", qty, pct, "Your target was reached. Reduce size to lock gains, or fully exit if that better matches your plan.")
    if recommendation_code == "REVIEW_URGENT" and pnl_pct is not None and pnl_pct > 0:
        qty = _qty_for_pct(urgent_trim_pct)
        pct = (urgent_trim_pct * Decimal("100")).quantize(Decimal("0.01"))
        return ("reduce exposure", qty, pct, "The setup is weakening while still profitable. Cutting partial size can protect gains during review.")
    if recommendation_code == "REVIEW":
        qty = _qty_for_pct(review_trim_pct)
        pct = (review_trim_pct * Decimal("100")).quantize(Decimal("0.01"))
        return ("consider light trim", qty, pct, "This is still a manual review posture, but trimming a small slice can reduce risk while you reassess.")
    return ("hold current size", None, None, "No trim or sell action is currently suggested.")

def build_holding_health_snapshot(position: HeldPosition) -> HoldingHealthSnapshot:
    position = refresh_position_market_state(position)
    current = Decimal(position.last_price) if position.last_price is not None else None
    profile = _get_position_risk_profile(position)
    opposing_signal = (
        Signal.objects.select_related("strategy")
        .filter(
            instrument=position.instrument,
            generated_at__gte=position.opened_at,
            status__in=[Signal.Status.NEW, Signal.Status.REVIEWED, Signal.Status.TAKEN, Signal.Status.CONFIRMED],
            direction=Signal.Direction.SHORT,
        )
        .order_by("-generated_at", "-id")
        .first()
    )
    stop_breached = bool(current is not None and position.stop_price is not None and current <= position.stop_price)
    target_reached = bool(current is not None and position.target_price is not None and current >= position.target_price)
    deterioration_limit = float(getattr(profile, "drawdown_urgent_pct", getattr(settings, "HELD_POSITION_DETERIORATION_ALERT_PCT", 5.0)) or 5.0)
    review_limit = float(getattr(profile, "drawdown_review_pct", getattr(settings, "HELD_POSITION_REVIEW_WARNING_PCT", 2.5)) or 2.5)
    deteriorating = bool(position.pnl_pct is not None and position.pnl_pct <= -abs(deterioration_limit))
    warning_drawdown = bool(position.pnl_pct is not None and position.pnl_pct <= -abs(review_limit))
    thesis_broken = opposing_signal is not None

    status_bits = []
    if stop_breached:
        status_bits.append("stop broken")
    if thesis_broken:
        status_bits.append("short signal")
    if deteriorating:
        status_bits.append("deep drawdown")
    elif warning_drawdown:
        status_bits.append("drawdown warning")
    if target_reached:
        status_bits.append("target hit")
    status_label = ", ".join(status_bits) if status_bits else "healthy"
    recommendation_code, recommendation_label, recommendation_reason, recommendation_rank = _build_recommendation(
        stop_breached=stop_breached,
        thesis_broken=thesis_broken,
        target_reached=target_reached,
        deteriorating=deteriorating,
        warning_drawdown=warning_drawdown,
        pnl_pct=position.pnl_pct,
    )
    suggested_action_label, suggested_action_quantity, suggested_action_pct, suggested_action_reason = _build_action_plan(
        position=position,
        recommendation_code=recommendation_code,
        pnl_pct=position.pnl_pct,
    )
    risk_guardrail = _build_risk_guardrail(position=position, current=current, stop_breached=stop_breached)

    return HoldingHealthSnapshot(
        position=position,
        current_price=current,
        pnl_pct=position.pnl_pct,
        status_label=status_label,
        thesis_broken=thesis_broken,
        stop_breached=stop_breached,
        target_reached=target_reached,
        deteriorating=deteriorating,
        warning_drawdown=warning_drawdown,
        opposing_signal=opposing_signal,
        recommendation_code=recommendation_code,
        recommendation_label=recommendation_label,
        recommendation_reason=recommendation_reason,
        recommendation_rank=recommendation_rank,
        suggested_action_label=suggested_action_label,
        suggested_action_quantity=suggested_action_quantity,
        suggested_action_pct=suggested_action_pct,
        suggested_action_reason=suggested_action_reason,
        missing_stop=risk_guardrail["missing_stop"],
        stop_too_wide=risk_guardrail["stop_too_wide"],
        near_stop=risk_guardrail["near_stop"],
        stop_loss_pct=risk_guardrail["stop_loss_pct"],
        risk_guardrail_posture=risk_guardrail["risk_guardrail_posture"],
        risk_guardrail_label=risk_guardrail["risk_guardrail_label"],
        risk_guardrail_reason=risk_guardrail["risk_guardrail_reason"],
        risk_guardrail_action=risk_guardrail["risk_guardrail_action"],
    )



def summarize_holding_risk_guardrails(*, user, account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    qs = HeldPosition.objects.select_related("instrument").filter(user=user, status=HeldPosition.Status.OPEN)
    if account_label == "__UNLABELED__":
        qs = qs.filter(account_label="")
    elif account_label:
        qs = qs.filter(account_label__iexact=account_label)
    snapshots = [build_holding_health_snapshot(item) for item in qs]
    rows = sorted(
        snapshots,
        key=lambda item: (0 if item.risk_guardrail_posture == "OVER" else 1 if item.risk_guardrail_posture == "NEAR" else 2, -item.recommendation_rank, item.position.instrument.symbol),
    )
    return {
        "count": len(snapshots),
        "over_count": sum(1 for item in snapshots if item.risk_guardrail_posture == "OVER"),
        "near_count": sum(1 for item in snapshots if item.risk_guardrail_posture == "NEAR"),
        "missing_stop_count": sum(1 for item in snapshots if item.missing_stop),
        "stop_too_wide_count": sum(1 for item in snapshots if item.stop_too_wide),
        "near_stop_count": sum(1 for item in snapshots if item.near_stop),
        "rows": rows[:8],
    }


def summarize_account_stop_guardrails(*, user) -> dict:
    positions = list(HeldPosition.objects.select_related("instrument").filter(user=user, status=HeldPosition.Status.OPEN).order_by("account_label", "instrument__symbol", "id"))
    buckets: dict[str, list[HoldingHealthSnapshot]] = {}
    for position in positions:
        label = (position.account_label or "").strip() or "Unlabeled / blended"
        buckets.setdefault(label, []).append(build_holding_health_snapshot(position))
    rows = []
    for account_label, snapshots in buckets.items():
        over_count = sum(1 for item in snapshots if item.risk_guardrail_posture == "OVER")
        near_count = sum(1 for item in snapshots if item.risk_guardrail_posture == "NEAR")
        overall_posture = "OVER" if over_count else ("NEAR" if near_count else "OK")
        hottest = None
        ranked = sorted(snapshots, key=lambda item: (0 if item.risk_guardrail_posture == "OVER" else 1 if item.risk_guardrail_posture == "NEAR" else 2, -item.recommendation_rank, item.position.instrument.symbol))
        if ranked:
            hottest = ranked[0]
        rows.append({
            "account_label": account_label,
            "holdings_url_account": "__UNLABELED__" if account_label == "Unlabeled / blended" else account_label,
            "overall_posture": overall_posture,
            "open_count": len(snapshots),
            "over_count": over_count,
            "near_count": near_count,
            "missing_stop_count": sum(1 for item in snapshots if item.missing_stop),
            "stop_too_wide_count": sum(1 for item in snapshots if item.stop_too_wide),
            "near_stop_count": sum(1 for item in snapshots if item.near_stop),
            "hottest": hottest,
        })
    rows.sort(key=lambda row: (0 if row["overall_posture"] == "OVER" else 1 if row["overall_posture"] == "NEAR" else 2, -row["open_count"], row["account_label"].lower()))
    return {
        "count": len(rows),
        "posture_counts": {
            "OVER": sum(1 for row in rows if row["overall_posture"] == "OVER"),
            "NEAR": sum(1 for row in rows if row["overall_posture"] == "NEAR"),
            "OK": sum(1 for row in rows if row["overall_posture"] == "OK"),
        },
        "rows": rows,
    }


def summarize_account_holding_queues(*, user) -> dict:
    positions = list(
        HeldPosition.objects.select_related("instrument")
        .filter(user=user, status=HeldPosition.Status.OPEN)
        .order_by("account_label", "instrument__symbol", "id")
    )
    buckets: dict[str, list[HoldingHealthSnapshot]] = {}
    for position in positions:
        label = (position.account_label or "").strip() or "Unlabeled / blended"
        buckets.setdefault(label, []).append(build_holding_health_snapshot(position))

    rows = []
    for account_label, snapshots in buckets.items():
        queue_counts = {
            "sell_now": sum(1 for item in snapshots if item.recommendation_code == "SELL_NOW"),
            "review_now": sum(1 for item in snapshots if item.recommendation_code in {"REVIEW", "REVIEW_URGENT"}),
            "trim_or_exit": sum(1 for item in snapshots if item.recommendation_code == "TRIM_OR_EXIT"),
            "missing_import": sum(1 for item in snapshots if item.position.missing_from_latest_import),
            "missing_stop": sum(1 for item in snapshots if item.missing_stop),
            "near_stop": sum(1 for item in snapshots if item.near_stop),
            "stop_breached": sum(1 for item in snapshots if item.stop_breached),
        }
        hot = sorted(
            snapshots,
            key=lambda item: (
                0 if item.recommendation_code == "SELL_NOW" else 1 if item.recommendation_code == "REVIEW_URGENT" else 2 if item.risk_guardrail_posture == "OVER" else 3,
                -item.recommendation_rank,
                item.position.instrument.symbol,
            ),
        )
        overall = "OVER" if (queue_counts["sell_now"] or queue_counts["stop_breached"] or queue_counts["missing_stop"]) else ("NEAR" if (queue_counts["review_now"] or queue_counts["near_stop"] or queue_counts["trim_or_exit"]) else "OK")
        holdings_url_account = "__UNLABELED__" if account_label == "Unlabeled / blended" else account_label
        rows.append({
            "account_label": account_label,
            "holdings_url_account": holdings_url_account,
            "overall_posture": overall,
            "open_count": len(snapshots),
            "queue_counts": queue_counts,
            "hot_symbols": [snap.position.instrument.symbol for snap in hot[:3]],
            "sell_now_url": f"/portfolios/holdings/?status=OPEN&recommendation=SELL_NOW&account={holdings_url_account}",
            "review_url": f"/portfolios/holdings/?status=OPEN&recommendation=REVIEW&account={holdings_url_account}",
            "guardrail_url": f"/portfolios/holdings/?status=OPEN&account={holdings_url_account}",
        })
    rows.sort(key=lambda row: (0 if row["overall_posture"] == "OVER" else 1 if row["overall_posture"] == "NEAR" else 2, -row["queue_counts"]["sell_now"], -row["open_count"], row["account_label"].lower()))
    return {
        "count": len(rows),
        "posture_counts": {
            "OVER": sum(1 for row in rows if row["overall_posture"] == "OVER"),
            "NEAR": sum(1 for row in rows if row["overall_posture"] == "NEAR"),
            "OK": sum(1 for row in rows if row["overall_posture"] == "OK"),
        },
        "rows": rows,
    }




def _format_hours_to_resolution(hours_value: float | None) -> str:
    if hours_value is None:
        return "—"
    if hours_value < 24:
        return f"{round(hours_value, 1)}h"
    return f"{round(hours_value / 24, 1)}d"


def _build_stop_policy_timeliness_stats(transactions: list[HoldingTransaction], *, target_hours: int) -> dict:
    rows = [
        tx for tx in transactions
        if tx.event_type in {HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD}
    ]
    immediate_count = 0
    on_time_count = 0
    late_count = 0
    pending_count = 0
    resolved_hours: list[float] = []
    for tx in rows:
        status = (tx.stop_policy_status or "").strip().upper()
        if status == "ON_TIME":
            on_time_count += 1
            if tx.stop_policy_resolved_at and tx.created_at and tx.stop_policy_resolved_at <= tx.created_at:
                immediate_count += 1
        elif status == "LATE":
            late_count += 1
        elif status == "PENDING":
            pending_count += 1
        if tx.stop_policy_resolved_at and tx.created_at:
            delta_hours = max(0.0, (tx.stop_policy_resolved_at - tx.created_at).total_seconds() / 3600)
            resolved_hours.append(delta_hours)
    total = len(rows)
    on_time_rate = round((on_time_count / total) * 100, 1) if total else None
    late_rate = round((late_count / total) * 100, 1) if total else None
    pending_rate = round((pending_count / total) * 100, 1) if total else None
    avg_resolution_hours = round(sum(resolved_hours) / len(resolved_hours), 1) if resolved_hours else None
    return {
        "count": total,
        "target_hours": target_hours,
        "immediate_count": immediate_count,
        "on_time_count": on_time_count,
        "late_count": late_count,
        "pending_count": pending_count,
        "on_time_rate": on_time_rate,
        "late_rate": late_rate,
        "pending_rate": pending_rate,
        "avg_resolution_hours": avg_resolution_hours,
        "avg_resolution_label": _format_hours_to_resolution(avg_resolution_hours),
    }


def summarize_stop_policy_timeliness(*, user, account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    profile, _ = UserRiskProfile.objects.get_or_create(user=user)
    target_hours = max(1, int(getattr(profile, "stop_policy_target_hours", 24) or 24))
    tx_qs = HoldingTransaction.objects.select_related("position", "position__instrument").filter(
        position__user=user,
        event_type__in={HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD},
    )
    if account_label == "__UNLABELED__":
        tx_qs = tx_qs.filter(account_label_snapshot="")
    elif account_label:
        tx_qs = tx_qs.filter(account_label_snapshot__iexact=account_label)
    transactions = list(tx_qs.order_by("-created_at", "-id")[:300])

    now = timezone.now()
    recent_30_cutoff = now - timedelta(days=30)
    recent_90_cutoff = now - timedelta(days=90)
    prior_30_start = now - timedelta(days=60)

    recent_30 = [tx for tx in transactions if tx.created_at >= recent_30_cutoff]
    recent_90 = [tx for tx in transactions if tx.created_at >= recent_90_cutoff]
    prior_30 = [tx for tx in transactions if prior_30_start <= tx.created_at < recent_30_cutoff]

    recent_30_stats = _build_stop_policy_timeliness_stats(recent_30, target_hours=target_hours)
    recent_90_stats = _build_stop_policy_timeliness_stats(recent_90, target_hours=target_hours)
    prior_30_stats = _build_stop_policy_timeliness_stats(prior_30, target_hours=target_hours)

    on_time_delta = None
    pending_delta = None
    if recent_30_stats["on_time_rate"] is not None and prior_30_stats["on_time_rate"] is not None:
        on_time_delta = round(recent_30_stats["on_time_rate"] - prior_30_stats["on_time_rate"], 1)
    if recent_30_stats["pending_rate"] is not None and prior_30_stats["pending_rate"] is not None:
        pending_delta = round(recent_30_stats["pending_rate"] - prior_30_stats["pending_rate"], 1)

    trend_direction = "FLAT"
    trend_label = "Stable"
    trend_reason = f"The app treats stops recorded or tightened within {target_hours}h of an open/add event as on-time policy follow-through."
    if on_time_delta is None and pending_delta is None:
        trend_direction = "NEW" if recent_30_stats["count"] else "NO_DATA"
        trend_label = "Not enough history" if recent_30_stats["count"] else "No recent open/add events"
    else:
        on_time_delta = on_time_delta or 0.0
        pending_delta = pending_delta or 0.0
        if on_time_delta >= 10 or pending_delta <= -10:
            trend_direction = "IMPROVING"
            trend_label = "Improving"
            trend_reason = f"Mike is getting stops recorded or tightened faster after new open/add events than in the prior 30-day window (target: {target_hours}h)."
        elif on_time_delta <= -10 or pending_delta >= 10:
            trend_direction = "DEGRADING"
            trend_label = "Degrading"
            trend_reason = f"Open/add events are spending longer without a recorded or tightened stop than in the prior 30-day window (target: {target_hours}h)."

    buckets = {}
    for tx in recent_30:
        label = (tx.account_label_snapshot or "").strip() or "Unlabeled / blended"
        buckets.setdefault(label, []).append(tx)
    account_rows = []
    for label, bucket in buckets.items():
        stats = _build_stop_policy_timeliness_stats(bucket, target_hours=target_hours)
        latest = bucket[0] if bucket else None
        overall = "OVER" if stats["pending_count"] or stats["late_count"] else ("NEAR" if stats["immediate_count"] < stats["count"] else "OK")
        account_rows.append({
            "account_label": label,
            "count": stats["count"],
            "on_time_rate": stats["on_time_rate"],
            "pending_rate": stats["pending_rate"],
            "late_count": stats["late_count"],
            "pending_count": stats["pending_count"],
            "immediate_count": stats["immediate_count"],
            "avg_resolution_label": stats["avg_resolution_label"],
            "overall_posture": overall,
            "latest": latest,
        })
    account_rows.sort(key=lambda row: (0 if row["overall_posture"] == "OVER" else 1 if row["overall_posture"] == "NEAR" else 2, -(row["pending_rate"] or 0), row["account_label"].lower()))

    recent_rows = []
    followup_summary = {"pending": 0, "overdue": 0, "late": 0, "on_time": 0}
    for tx in recent_30[:10]:
        status = (tx.stop_policy_status or "").strip().upper() or "UNRECORDED"
        hours_to_resolution = None
        if tx.stop_policy_resolved_at and tx.created_at:
            hours_to_resolution = max(0.0, (tx.stop_policy_resolved_at - tx.created_at).total_seconds() / 3600)
        recent_rows.append({
            "tx": tx,
            "account_label": (tx.account_label_snapshot or "").strip() or "Unlabeled / blended",
            "status": status,
            "resolution_label": _format_hours_to_resolution(hours_to_resolution),
        })
    for tx in recent_30:
        bucket = _derive_stop_policy_followup_bucket(tx, now=now)["bucket"]
        if bucket == "PENDING_ACTIVE":
            followup_summary["pending"] += 1
        elif bucket == "PENDING_OVERDUE":
            followup_summary["overdue"] += 1
        elif bucket == "LATE_RESOLVED":
            followup_summary["late"] += 1
        elif bucket == "ON_TIME":
            followup_summary["on_time"] += 1

    return {
        "target_hours": target_hours,
        "recent_30": recent_30_stats,
        "recent_90": recent_90_stats,
        "prior_30": prior_30_stats,
        "trend_direction": trend_direction,
        "trend_label": trend_label,
        "trend_reason": trend_reason,
        "on_time_delta": on_time_delta,
        "pending_rate_delta": pending_delta,
        "account_rows": account_rows[:8],
        "recent_rows": recent_rows,
        "followup_summary": followup_summary,
    }


def _derive_stop_policy_followup_bucket(tx: HoldingTransaction, *, now=None) -> dict:
    now = now or timezone.now()
    status = (tx.stop_policy_status or "").strip().upper() or "UNRECORDED"
    due_at = tx.stop_policy_due_at
    resolved_at = tx.stop_policy_resolved_at
    hours_open = None
    if tx.created_at:
        hours_open = max(0.0, (now - tx.created_at).total_seconds() / 3600)

    bucket = status
    bucket_label = status.replace("_", " ").title()
    actionable = False
    if status == "PENDING":
        actionable = True
        if due_at and now > due_at:
            bucket = "PENDING_OVERDUE"
            bucket_label = "Pending overdue"
        else:
            bucket = "PENDING_ACTIVE"
            bucket_label = "Pending"
    elif status == "LATE":
        actionable = True
        bucket = "LATE_RESOLVED"
        bucket_label = "Late resolved"
    elif status == "ON_TIME":
        bucket = "ON_TIME"
        bucket_label = "On time"
    return {
        "status": status,
        "bucket": bucket,
        "bucket_label": bucket_label,
        "actionable": actionable,
        "hours_open": round(hours_open, 1) if hours_open is not None else None,
        "resolved_at": resolved_at,
    }




def _has_execution_evidence(tx: HoldingTransaction) -> bool:
    return bool(
        (tx.execution_evidence_type or "").strip()
        or (tx.execution_evidence_reference or "").strip()
        or (tx.execution_evidence_note or "").strip()
        or tx.execution_evidence_recorded_at
        or bool(getattr(tx, "execution_evidence_attachment", None))
        or getattr(tx, "broker_confirmation_snapshot_id", None)
        or getattr(tx, "broker_confirmation_run_id", None)
        or getattr(tx, "broker_confirmation_resolution_id", None)
    )


def _execution_evidence_label(tx: HoldingTransaction) -> str:
    evidence_type = (tx.execution_evidence_type or "").strip()
    if evidence_type:
        choice_map = dict(HoldingTransaction.ExecutionEvidenceType.choices)
        return choice_map.get(evidence_type, evidence_type.replace("_", " ").title())
    if getattr(tx, "broker_confirmation_resolution_id", None):
        return "Linked broker confirmation resolution"
    if getattr(tx, "broker_confirmation_run_id", None):
        return "Linked broker reconciliation run"
    if getattr(tx, "broker_confirmation_snapshot_id", None):
        return "Linked broker account snapshot"
    if getattr(tx, "execution_evidence_attachment", None):
        return "Execution evidence attachment saved"
    if tx.execution_evidence_recorded_at:
        return "Execution evidence saved"
    return ""


def _execution_evidence_quality_label(tx: HoldingTransaction) -> str:
    quality = (tx.execution_evidence_quality or "").strip()
    if quality:
        choice_map = dict(HoldingTransaction.ExecutionEvidenceQuality.choices)
        return choice_map.get(quality, quality.replace("_", " ").title())
    if _has_execution_evidence(tx):
        return "Unrated"
    return ""


def _execution_evidence_quality_rank(tx: HoldingTransaction) -> int:
    quality = (tx.execution_evidence_quality or "").strip().upper()
    return {
        "VERIFIED": 4,
        "STRONG": 3,
        "WEAK": 2,
        "PLACEHOLDER": 1,
    }.get(quality, 0)


def _collect_stop_policy_exception_period_rows(transactions: list[HoldingTransaction], *, now=None) -> list[dict]:
    now = now or timezone.now()
    rows = []
    for tx in transactions:
        if tx.event_type not in {HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD}:
            continue
        derived = _derive_stop_policy_followup_bucket(tx, now=now)
        bucket = derived["bucket"]
        reason_code = (tx.stop_policy_reason_code or "").strip()
        if not reason_code and bucket not in {"PENDING_OVERDUE", "LATE_RESOLVED", "PENDING_ACTIVE"}:
            continue
        has_attachment = bool(getattr(tx, "execution_evidence_attachment", None))
        rows.append({
            "tx": tx,
            "bucket": bucket,
            "reason_code": reason_code,
            "reason_label": tx.get_stop_policy_reason_code_display() if reason_code else "No audit reason saved",
            "symbol": tx.position.instrument.symbol,
            "account_label": (tx.account_label_snapshot or "").strip() or "Unlabeled / blended",
            "has_execution_evidence": _has_execution_evidence(tx),
            "execution_evidence_label": _execution_evidence_label(tx),
            "execution_evidence_quality": (tx.execution_evidence_quality or "").strip(),
            "execution_evidence_quality_label": _execution_evidence_quality_label(tx),
            "execution_evidence_quality_rank": _execution_evidence_quality_rank(tx),
            "has_execution_evidence_attachment": has_attachment,
            "broker_confirmation_linked": bool(getattr(tx, "broker_confirmation_snapshot_id", None) or getattr(tx, "broker_confirmation_run_id", None) or getattr(tx, "broker_confirmation_resolution_id", None)),
        })
    return rows

def resolve_evidence_retention_days(*, risk_profile, evidence_type: str = "", evidence_quality: str = "", has_attachment: bool = True, user=None, account_label: str = "") -> tuple[int | None, str]:
    if not has_attachment:
        return None, "no_attachment"

    account_label = (account_label or "").strip()
    account_override = None
    if user is not None and account_label:
        try:
            account_override = AccountRetentionPolicyOverride.objects.filter(user=user, account_label__iexact=account_label).first()
        except Exception:
            account_override = None

    def _resolved_days(field_name: str, fallback: int) -> tuple[int, str]:
        override_value = None
        if account_override is not None:
            override_value = getattr(account_override, field_name, None)
        if override_value:
            return max(1, int(override_value)), f"account:{account_label}:{field_name}"
        return max(1, int(getattr(risk_profile, field_name, fallback) or fallback)), f"global:{field_name}"

    default_days, default_source = _resolved_days("evidence_retention_default_days", 365)
    days = default_days
    source = default_source

    quality = (evidence_quality or "").strip().upper()
    quality_field_map = {
        HoldingTransaction.ExecutionEvidenceQuality.VERIFIED: "evidence_retention_verified_days",
        HoldingTransaction.ExecutionEvidenceQuality.STRONG: "evidence_retention_strong_days",
        HoldingTransaction.ExecutionEvidenceQuality.WEAK: "evidence_retention_weak_days",
        HoldingTransaction.ExecutionEvidenceQuality.PLACEHOLDER: "evidence_retention_placeholder_days",
    }
    if quality in quality_field_map:
        quality_days, quality_source = _resolved_days(quality_field_map[quality], default_days)
        if quality_days != days:
            days = quality_days
            source = quality_source

    evidence_type = (evidence_type or "").strip().upper()
    type_field_map = {
        HoldingTransaction.ExecutionEvidenceType.BROKER_CONFIRMATION: "evidence_retention_confirmation_days",
        HoldingTransaction.ExecutionEvidenceType.ORDER_REFERENCE: "evidence_retention_confirmation_days",
        HoldingTransaction.ExecutionEvidenceType.IMPORT_MATCH: "evidence_retention_import_match_days",
    }
    if evidence_type in type_field_map:
        type_days, type_source = _resolved_days(type_field_map[evidence_type], days)
        if type_days > days:
            days = type_days
            source = type_source

    return days, source



def _build_stop_policy_reason_playbook(*, code: str, label: str, bucket: dict, recurring_symbol_rows: list[dict]) -> dict:
    code = (code or "").strip()
    recurring_for_reason = [row for row in recurring_symbol_rows if row.get("top_reason_code") == code]
    recurring_ready = sum(1 for row in recurring_for_reason if row.get("needs_action"))

    title = label or "Unspecified reason"
    workflow = "Open the filtered queue and work the next stop update directly from the holding workflow."
    focus = "Review the newest rows first and clean up anything still pending."
    action_label = "Open filtered queue"
    status_filter = "ACTIONABLE" if bucket.get("overdue") or bucket.get("pending") else "ALL"

    if code == HoldingTransaction.StopPolicyReasonCode.WAITING_CONFIRMATION:
        workflow = "Start with overdue names, then tighten stops on any open holdings that already have confirmation."
        focus = "Best for names that were intentionally paused while Mike waited for a setup or confirmation candle."
        action_label = "Work confirmation queue"
        status_filter = "OVERDUE" if bucket.get("overdue") else "PENDING"
    elif code == HoldingTransaction.StopPolicyReasonCode.BROKER_OR_IMPORT_DELAY:
        workflow = "Use the queue for broker/import-delay rows, confirm the broker state, then record the stop or note the still-open delay."
        focus = "Keeps broker-lag exceptions separate from genuine discipline misses."
        action_label = "Review delayed imports"
        status_filter = "PENDING"
    elif code == HoldingTransaction.StopPolicyReasonCode.INTENTIONAL_DEFER:
        workflow = "Review every intentional defer row and either tighten the stop now or leave a cleaner note explaining why the defer still stands."
        focus = "Useful when Mike wants to reduce stale discretionary exceptions instead of letting them quietly accumulate."
        action_label = "Audit defer decisions"
        status_filter = "ALL"
    elif code == HoldingTransaction.StopPolicyReasonCode.EXISTING_PLAN_OUTSIDE_APP:
        workflow = "Check that the outside-the-app stop plan still exists, then either mirror it inside the app or renew the exception note."
        focus = "Pushes external stop plans back into the tracked workflow when they keep reappearing."
        action_label = "Review outside-plan rows"
        status_filter = "ALL"
    elif code == HoldingTransaction.StopPolicyReasonCode.SCALING_EXCEPTION:
        workflow = "Review scaled entries and make sure the latest add has an updated stop instead of relying on the original position plan."
        focus = "Best when staged adds keep creating new stop debt on the same holding."
        action_label = "Work scaling exceptions"
        status_filter = "ACTIONABLE"
    elif code == HoldingTransaction.StopPolicyReasonCode.MANUAL_REVIEW:
        workflow = "Triage manual-review rows one by one, starting with overdue items, and either set the stop or capture a clearer operator note."
        focus = "Keeps vague manual-review debt from becoming a permanent parking lot."
        action_label = "Triage manual reviews"
        status_filter = "OVERDUE" if bucket.get("overdue") else "ACTIONABLE"
    elif code == HoldingTransaction.StopPolicyReasonCode.OTHER:
        workflow = "Open the filtered queue, look for a shared pattern, and convert repeat items to a tighter reason code when possible."
        focus = "The goal is to shrink the generic bucket over time."
        action_label = "Clean up generic reasons"
        status_filter = "ALL"
    elif code == "__UNSPECIFIED__":
        title = "No reason saved"
        workflow = "Open rows with no saved reason, add the right code, then handle the stop directly from the queue."
        focus = "This is the fastest way to improve audit coverage before more exceptions pile up."
        action_label = "Fill missing reasons"
        status_filter = "ACTIONABLE"

    return {
        "code": code,
        "label": title,
        "count": bucket.get("count", 0),
        "overdue": bucket.get("overdue", 0),
        "pending": bucket.get("pending", 0),
        "late": bucket.get("late", 0),
        "symbol_count": len(bucket.get("symbols", set())),
        "account_count": len(bucket.get("accounts", set())),
        "workflow": workflow,
        "focus": focus,
        "action_label": action_label,
        "status_filter": status_filter,
        "recurring_symbol_count": len(recurring_for_reason),
        "recurring_ready_count": recurring_ready,
        "priority_score": (bucket.get("overdue", 0) * 5) + (bucket.get("pending", 0) * 3) + (bucket.get("late", 0) * 2) + bucket.get("count", 0) + (recurring_ready * 2),
    }


def summarize_stop_policy_exception_trends(*, user, account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    now = timezone.now()
    tx_qs = HoldingTransaction.objects.select_related("position", "position__instrument").filter(
        position__user=user,
        event_type__in={HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD},
    )
    if account_label == "__UNLABELED__":
        tx_qs = tx_qs.filter(account_label_snapshot="")
    elif account_label:
        tx_qs = tx_qs.filter(account_label_snapshot__iexact=account_label)

    transactions = list(tx_qs.order_by("-created_at", "-id")[:500])
    recent_30_cutoff = now - timedelta(days=30)
    recent_90_cutoff = now - timedelta(days=90)
    prior_30_start = now - timedelta(days=60)

    recent_30 = [tx for tx in transactions if tx.created_at >= recent_30_cutoff]
    recent_90 = [tx for tx in transactions if tx.created_at >= recent_90_cutoff]
    prior_30 = [tx for tx in transactions if prior_30_start <= tx.created_at < recent_30_cutoff]

    recent_rows = _collect_stop_policy_exception_period_rows(recent_30, now=now)
    prior_rows = _collect_stop_policy_exception_period_rows(prior_30, now=now)
    recent_90_rows = _collect_stop_policy_exception_period_rows(recent_90, now=now)

    def _by_reason(rows: list[dict]) -> dict[str, dict]:
        out = {}
        for row in rows:
            code = row["reason_code"] or "__UNSPECIFIED__"
            bucket = out.setdefault(code, {
                "code": code,
                "label": row["reason_label"],
                "count": 0,
                "overdue": 0,
                "pending": 0,
                "late": 0,
                "symbols": set(),
                "accounts": set(),
            })
            bucket["count"] += 1
            bucket["symbols"].add(row["symbol"])
            bucket["accounts"].add(row["account_label"])
            if row["bucket"] == "PENDING_OVERDUE":
                bucket["overdue"] += 1
            elif row["bucket"] == "PENDING_ACTIVE":
                bucket["pending"] += 1
            elif row["bucket"] == "LATE_RESOLVED":
                bucket["late"] += 1
        return out

    recent_reason_map = _by_reason(recent_rows)
    prior_reason_map = _by_reason(prior_rows)

    reason_rows = []
    reason_sla_rows = []
    for code, bucket in recent_reason_map.items():
        prior_count = prior_reason_map.get(code, {}).get("count", 0)
        pending_hours = []
        oldest_pending_hours = None
        actionable_count = 0
        overdue_actionable = 0
        pending_active = 0
        late_resolved = 0
        for row in recent_rows:
            row_code = row["reason_code"] or "__UNSPECIFIED__"
            if row_code != code:
                continue
            age_hours = max(0.0, round((now - row["tx"].created_at).total_seconds() / 3600, 1))
            if row["bucket"] == "PENDING_OVERDUE":
                actionable_count += 1
                overdue_actionable += 1
                pending_hours.append(age_hours)
            elif row["bucket"] == "PENDING_ACTIVE":
                actionable_count += 1
                pending_active += 1
                pending_hours.append(age_hours)
            elif row["bucket"] == "LATE_RESOLVED":
                late_resolved += 1
        if pending_hours:
            oldest_pending_hours = max(pending_hours)
        avg_pending_hours = round(sum(pending_hours) / len(pending_hours), 1) if pending_hours else None
        overdue_rate = round((overdue_actionable / actionable_count) * 100, 1) if actionable_count else None
        with_evidence_count = sum(1 for row in recent_rows if (row["reason_code"] or "__UNSPECIFIED__") == code and row.get("has_execution_evidence"))
        without_evidence_count = sum(1 for row in recent_rows if (row["reason_code"] or "__UNSPECIFIED__") == code and not row.get("has_execution_evidence"))
        reason_rows.append({
            "code": code,
            "label": bucket["label"],
            "count": bucket["count"],
            "delta": bucket["count"] - prior_count,
            "overdue": bucket["overdue"],
            "pending": bucket["pending"],
            "late": bucket["late"],
            "symbol_count": len(bucket["symbols"]),
            "account_count": len(bucket["accounts"]),
            "with_evidence_count": with_evidence_count,
            "without_evidence_count": without_evidence_count,
        })
        evidence_actionable_count = sum(1 for row in recent_rows if (row["reason_code"] or "__UNSPECIFIED__") == code and row["bucket"] in {"PENDING_OVERDUE", "PENDING_ACTIVE"} and row.get("has_execution_evidence"))
        reason_sla_rows.append({
            "code": code,
            "label": bucket["label"],
            "actionable_count": actionable_count,
            "overdue": overdue_actionable,
            "pending_active": pending_active,
            "late": late_resolved,
            "avg_pending_hours": avg_pending_hours,
            "oldest_pending_hours": oldest_pending_hours,
            "overdue_rate": overdue_rate,
            "symbol_count": len(bucket["symbols"]),
            "account_count": len(bucket["accounts"]),
            "evidence_actionable_count": evidence_actionable_count,
            "priority_score": (overdue_actionable * 5) + (pending_active * 3) + (late_resolved * 2) + (int(oldest_pending_hours or 0) // 24),
        })
    reason_rows.sort(key=lambda row: (-row["count"], -row["overdue"], row["label"].lower()))
    reason_sla_rows.sort(key=lambda row: (-row["priority_score"], -(row["oldest_pending_hours"] or 0), row["label"].lower()))

    account_map = {}
    for row in recent_rows:
        bucket = account_map.setdefault(row["account_label"], {
            "account_label": row["account_label"],
            "count": 0,
            "with_reason": 0,
            "overdue": 0,
            "late": 0,
            "top_reason_counts": {},
        })
        bucket["count"] += 1
        if row["reason_code"]:
            bucket["with_reason"] += 1
            bucket["top_reason_counts"][row["reason_label"]] = bucket["top_reason_counts"].get(row["reason_label"], 0) + 1
        if row["bucket"] == "PENDING_OVERDUE":
            bucket["overdue"] += 1
        elif row["bucket"] == "LATE_RESOLVED":
            bucket["late"] += 1
    account_rows = []
    for bucket in account_map.values():
        top_reason = None
        if bucket["top_reason_counts"]:
            top_reason = sorted(bucket["top_reason_counts"].items(), key=lambda item: (-item[1], item[0].lower()))[0][0]
        account_rows.append({
            **bucket,
            "coverage_rate": round((bucket["with_reason"] / bucket["count"]) * 100, 1) if bucket["count"] else None,
            "top_reason": top_reason or "—",
        })
    account_rows.sort(key=lambda row: (-row["count"], -row["overdue"], row["account_label"].lower()))

    symbol_map = {}
    for row in recent_90_rows:
        key = row["symbol"]
        bucket = symbol_map.setdefault(key, {
            "symbol": row["symbol"],
            "count": 0,
            "accounts": set(),
            "reasons": {},
            "reason_labels": {},
            "latest_tx": row["tx"],
            "overdue": 0,
            "late": 0,
        })
        bucket["count"] += 1
        bucket["accounts"].add(row["account_label"])
        reason_code = row["reason_code"] or "__UNSPECIFIED__"
        bucket["reasons"][reason_code] = bucket["reasons"].get(reason_code, 0) + 1
        bucket["reason_labels"][reason_code] = row["reason_label"]
        if row["tx"].created_at > bucket["latest_tx"].created_at:
            bucket["latest_tx"] = row["tx"]
        if row["bucket"] == "PENDING_OVERDUE":
            bucket["overdue"] += 1
        elif row["bucket"] == "LATE_RESOLVED":
            bucket["late"] += 1
    recurring_symbol_rows = []
    for bucket in symbol_map.values():
        if bucket["count"] < 2:
            continue
        latest_tx = bucket["latest_tx"]
        latest_derived = _derive_stop_policy_followup_bucket(latest_tx, now=now)
        position = latest_tx.position
        holding_open = position.status == HeldPosition.Status.OPEN
        needs_action = bool(latest_derived["actionable"] and holding_open)
        top_reason_code = sorted(bucket["reasons"].items(), key=lambda item: (-item[1], item[0].lower()))[0][0]
        top_reason = bucket["reason_labels"].get(top_reason_code, "No audit reason saved")
        recurring_symbol_rows.append({
            "symbol": bucket["symbol"],
            "count": bucket["count"],
            "accounts": sorted(bucket["accounts"]),
            "account_label": ", ".join(sorted(bucket["accounts"])),
            "top_reason": top_reason,
            "top_reason_code": top_reason_code,
            "latest_tx": latest_tx,
            "latest_bucket": latest_derived["bucket"],
            "latest_bucket_label": latest_derived["bucket_label"],
            "latest_account_label": (latest_tx.account_label_snapshot or "").strip() or "Unlabeled / blended",
            "position_id": position.pk,
            "holding_open": holding_open,
            "needs_action": needs_action,
            "remediation_label": "Record / tighten stop" if needs_action else ("Review holding" if holding_open else "Review history"),
            "current_stop_price": position.stop_price,
            "overdue": bucket["overdue"],
            "late": bucket["late"],
            "actionable_count": bucket["overdue"] + sum(1 for row in recent_90_rows if row["symbol"] == bucket["symbol"] and row["bucket"] == "PENDING_ACTIVE"),
        })
    recurring_symbol_rows.sort(key=lambda row: (-row["actionable_count"], -row["count"], -row["overdue"], row["symbol"]))

    recent_count = len(recent_rows)
    prior_count = len(prior_rows)
    exception_delta = recent_count - prior_count if (recent_count or prior_count) else None
    recurring_count = len(recurring_symbol_rows)
    trend_label = "Stable"
    trend_direction = "FLAT"
    trend_reason = "Reason-code usage and recurring defer patterns help Mike distinguish one-off stop-policy misses from repeated workflow debt."
    if recent_count == 0:
        trend_label = "No recent exception history"
        trend_direction = "NO_DATA"
    elif exception_delta is not None and exception_delta >= 3:
        trend_label = "Exception debt rising"
        trend_direction = "DEGRADING"
        trend_reason = "More stop-policy exceptions were logged in the last 30 days than in the prior 30-day window, so defer patterns are becoming more frequent."
    elif exception_delta is not None and exception_delta <= -3:
        trend_label = "Exception debt easing"
        trend_direction = "IMPROVING"
        trend_reason = "Fewer stop-policy exceptions were logged in the last 30 days than in the prior 30-day window, so defer patterns are cooling off."
    elif recurring_count:
        trend_label = "Recurring defer patterns present"
        trend_direction = "WATCH"
        trend_reason = "Some symbols have shown repeated stop-policy exceptions inside the last 90 days, which usually points to repeat workflow friction or repeated intentional exceptions."

    remediation_ready_count = sum(1 for row in recurring_symbol_rows if row["needs_action"])
    playbook_rows = [
        _build_stop_policy_reason_playbook(code=code, label=bucket["label"], bucket=bucket, recurring_symbol_rows=recurring_symbol_rows)
        for code, bucket in recent_reason_map.items()
    ]
    playbook_rows.sort(key=lambda row: (-row["priority_score"], row["label"].lower()))

    actionable_recent_count = sum(1 for row in recent_rows if row["bucket"] in {"PENDING_ACTIVE", "PENDING_OVERDUE"})
    overdue_recent_count = sum(1 for row in recent_rows if row["bucket"] == "PENDING_OVERDUE")
    late_recent_count = sum(1 for row in recent_rows if row["bucket"] == "LATE_RESOLVED")
    top_sla_reason = reason_sla_rows[0] if reason_sla_rows else None
    top_playbook = playbook_rows[0] if playbook_rows else None
    waiting_rows = [row for row in recent_rows if (row["reason_code"] or "__UNSPECIFIED__") == HoldingTransaction.StopPolicyReasonCode.WAITING_CONFIRMATION]
    waiting_with_evidence = sum(1 for row in waiting_rows if row.get("has_execution_evidence"))
    waiting_without_evidence = sum(1 for row in waiting_rows if not row.get("has_execution_evidence"))
    def _bucket_evidence(rows: list[dict]) -> dict[str, dict]:
        bucket_map = {}
        for row in rows:
            evidence_type = (row.get("execution_evidence_type") or "").strip() or "__NONE__"
            bucket = bucket_map.setdefault(evidence_type, {
                "code": evidence_type,
                "label": dict(HoldingTransaction.ExecutionEvidenceType.choices).get(evidence_type, "No evidence saved" if evidence_type == "__NONE__" else evidence_type.replace("_", " ").title()),
                "count": 0,
                "supported": 0,
                "unsupported": 0,
                "overdue": 0,
                "pending": 0,
                "late": 0,
                "symbols": set(),
                "accounts": set(),
            })
            bucket["count"] += 1
            bucket["symbols"].add(row["symbol"])
            bucket["accounts"].add(row["account_label"])
            if row.get("has_execution_evidence"):
                bucket["supported"] += 1
            else:
                bucket["unsupported"] += 1
            if row["bucket"] == "PENDING_OVERDUE":
                bucket["overdue"] += 1
            elif row["bucket"] == "PENDING_ACTIVE":
                bucket["pending"] += 1
            elif row["bucket"] == "LATE_RESOLVED":
                bucket["late"] += 1
        return bucket_map

    recent_evidence_map = _bucket_evidence(recent_rows)
    prior_evidence_map = _bucket_evidence(prior_rows)
    evidence_trend_rows = []
    for code, bucket in recent_evidence_map.items():
        prior_bucket = prior_evidence_map.get(code, {})
        count = bucket["count"]
        supported = bucket["supported"]
        unsupported = bucket["unsupported"]
        actionable = bucket["overdue"] + bucket["pending"]
        support_rate = round((supported / count) * 100, 1) if count else None
        evidence_trend_rows.append({
            "code": code,
            "label": bucket["label"],
            "count": count,
            "delta": count - prior_bucket.get("count", 0),
            "supported": supported,
            "unsupported": unsupported,
            "support_rate": support_rate,
            "overdue": bucket["overdue"],
            "pending": bucket["pending"],
            "late": bucket["late"],
            "actionable": actionable,
            "symbol_count": len(bucket["symbols"]),
            "account_count": len(bucket["accounts"]),
            "priority_score": (bucket["overdue"] * 5) + (bucket["pending"] * 3) + unsupported + count,
        })
    evidence_trend_rows.sort(key=lambda row: (-row["priority_score"], -row["count"], row["label"].lower()))

    def _bucket_evidence_quality(rows: list[dict]) -> dict[str, dict]:
        bucket_map = {}
        quality_labels = dict(HoldingTransaction.ExecutionEvidenceQuality.choices)
        for row in rows:
            code = (row.get("execution_evidence_quality") or "").strip().upper() or "__UNRATED__"
            bucket = bucket_map.setdefault(code, {
                "code": code,
                "label": quality_labels.get(code, "Unrated" if code == "__UNRATED__" else code.replace("_", " ").title()),
                "count": 0,
                "actionable": 0,
                "overdue": 0,
                "pending": 0,
                "late": 0,
                "symbols": set(),
                "accounts": set(),
                "supported": 0,
                "unsupported": 0,
            })
            bucket["count"] += 1
            bucket["symbols"].add(row["symbol"])
            bucket["accounts"].add(row["account_label"])
            if row.get("has_execution_evidence"):
                bucket["supported"] += 1
            else:
                bucket["unsupported"] += 1
            if row["bucket"] == "PENDING_OVERDUE":
                bucket["overdue"] += 1
                bucket["actionable"] += 1
            elif row["bucket"] == "PENDING_ACTIVE":
                bucket["pending"] += 1
                bucket["actionable"] += 1
            elif row["bucket"] == "LATE_RESOLVED":
                bucket["late"] += 1
        return bucket_map

    recent_quality_map = _bucket_evidence_quality(recent_rows)
    prior_quality_map = _bucket_evidence_quality(prior_rows)
    evidence_quality_rows = []
    for code, bucket in recent_quality_map.items():
        prior_bucket = prior_quality_map.get(code, {})
        count = bucket["count"]
        support_rate = round((bucket["supported"] / count) * 100, 1) if count else None
        evidence_quality_rows.append({
            "code": code,
            "label": bucket["label"],
            "count": count,
            "delta": count - prior_bucket.get("count", 0),
            "actionable": bucket["actionable"],
            "overdue": bucket["overdue"],
            "pending": bucket["pending"],
            "late": bucket["late"],
            "supported": bucket["supported"],
            "unsupported": bucket["unsupported"],
            "support_rate": support_rate,
            "symbol_count": len(bucket["symbols"]),
            "account_count": len(bucket["accounts"]),
            "priority_score": (bucket["overdue"] * 6) + (bucket["pending"] * 4) + (bucket["unsupported"] * 3) + count,
        })
    evidence_quality_rows.sort(key=lambda row: (-row["priority_score"], -row["count"], row["label"].lower()))

    unsupported_waiting_count = sum(1 for row in waiting_rows if not row.get("has_execution_evidence"))
    waiting_linked_count = sum(1 for row in waiting_rows if row.get("broker_confirmation_linked"))
    waiting_linked_without_other_evidence = sum(1 for row in waiting_rows if row.get("broker_confirmation_linked") and not row.get("has_execution_evidence"))
    evidence_recent_count = sum(1 for row in recent_rows if row.get("has_execution_evidence"))
    evidence_prior_count = sum(1 for row in prior_rows if row.get("has_execution_evidence"))
    evidence_count_delta = evidence_recent_count - evidence_prior_count if (evidence_recent_count or evidence_prior_count) else None
    top_evidence_row = evidence_trend_rows[0] if evidence_trend_rows else None
    top_quality_row = evidence_quality_rows[0] if evidence_quality_rows else None
    operations_summary = {
        "actionable_recent_count": actionable_recent_count,
        "overdue_recent_count": overdue_recent_count,
        "late_recent_count": late_recent_count,
        "reasons_with_actionable": sum(1 for row in reason_sla_rows if row["actionable_count"]),
        "stale_reason_count": sum(1 for row in reason_sla_rows if (row["oldest_pending_hours"] or 0) >= 24),
        "top_sla_reason_label": top_sla_reason["label"] if top_sla_reason else None,
        "top_sla_reason_code": top_sla_reason["code"] if top_sla_reason else None,
        "top_sla_reason_overdue": top_sla_reason["overdue"] if top_sla_reason else 0,
        "top_sla_reason_oldest_hours": top_sla_reason["oldest_pending_hours"] if top_sla_reason else None,
        "top_playbook_label": top_playbook["label"] if top_playbook else None,
        "top_playbook_code": top_playbook["code"] if top_playbook else None,
        "waiting_confirmation_count": len(waiting_rows),
        "waiting_confirmation_with_evidence": waiting_with_evidence,
        "waiting_confirmation_without_evidence": waiting_without_evidence,
        "unsupported_waiting_confirmation_count": unsupported_waiting_count,
        "waiting_confirmation_linked_count": waiting_linked_count,
        "waiting_confirmation_linked_without_other_evidence": waiting_linked_without_other_evidence,
        "broker_linked_recent_count": sum(1 for row in recent_rows if row.get("broker_confirmation_linked")),
        "broker_linked_actionable_count": sum(1 for row in recent_rows if row.get("broker_confirmation_linked") and row.get("bucket") in {"PENDING_ACTIVE", "PENDING_OVERDUE"}),
        "evidence_recent_count": evidence_recent_count,
        "evidence_prior_count": evidence_prior_count,
        "evidence_count_delta": evidence_count_delta,
        "evidence_coverage_rate": round((evidence_recent_count / recent_count) * 100, 1) if recent_count else None,
        "top_evidence_label": top_evidence_row["label"] if top_evidence_row else None,
        "top_evidence_actionable": top_evidence_row["actionable"] if top_evidence_row else 0,
        "top_evidence_unsupported": top_evidence_row["unsupported"] if top_evidence_row else 0,
        "top_quality_label": top_quality_row["label"] if top_quality_row else None,
        "top_quality_code": top_quality_row["code"] if top_quality_row else None,
        "top_quality_actionable": top_quality_row["actionable"] if top_quality_row else 0,
        "top_quality_unsupported": top_quality_row["unsupported"] if top_quality_row else 0,
        "weak_quality_count": sum(1 for row in recent_rows if (row.get("execution_evidence_quality") or "").strip().upper() == "WEAK"),
        "placeholder_quality_count": sum(1 for row in recent_rows if (row.get("execution_evidence_quality") or "").strip().upper() == "PLACEHOLDER"),
        "unrated_evidence_count": sum(1 for row in recent_rows if row.get("has_execution_evidence") and not (row.get("execution_evidence_quality") or "").strip()),
        "strong_or_verified_count": sum(1 for row in recent_rows if (row.get("execution_evidence_quality") or "").strip().upper() in {"STRONG", "VERIFIED"}),
        "attachment_recent_count": sum(1 for row in recent_rows if row.get("has_execution_evidence_attachment")),
        "attachment_missing_count": sum(1 for row in recent_rows if row.get("has_execution_evidence") and not row.get("has_execution_evidence_attachment")),
        "attachment_expired_count": sum(1 for row in recent_rows if row.get("attachment_retention_expired")),
        "attachment_expiring_soon_count": sum(1 for row in recent_rows if row.get("attachment_retention_expiring_soon")),
    }

    return {
        "recent_30_count": recent_count,
        "prior_30_count": prior_count,
        "exception_delta": exception_delta,
        "recurring_symbol_count": recurring_count,
        "remediation_ready_count": remediation_ready_count,
        "trend_label": trend_label,
        "trend_direction": trend_direction,
        "trend_reason": trend_reason,
        "reason_rows": reason_rows[:8],
        "reason_sla_rows": reason_sla_rows[:6],
        "playbook_rows": playbook_rows[:6],
        "account_rows": account_rows[:8],
        "recurring_symbol_rows": recurring_symbol_rows[:8],
        "evidence_trend_rows": evidence_trend_rows[:6],
        "evidence_quality_rows": evidence_quality_rows[:6],
        "operations_summary": operations_summary,
        "audit_coverage_rate": round((sum(1 for row in recent_rows if row["reason_code"]) / recent_count) * 100, 1) if recent_count else None,
    }


def summarize_account_retention_overrides(*, user) -> dict:
    overrides = list(AccountRetentionPolicyOverride.objects.filter(user=user).order_by("account_label"))
    rows = []
    for item in overrides:
        active_fields = []
        for field_name, label in RETENTION_POLICY_FIELDS:
            value = getattr(item, field_name, None)
            if value:
                active_fields.append({"label": label, "days": value})
        rows.append({
            "override": item,
            "account_label": item.account_label,
            "active_fields": active_fields,
            "active_count": len(active_fields),
            "longest_days": max((field["days"] for field in active_fields), default=None),
            "shortest_days": min((field["days"] for field in active_fields), default=None),
        })
    return {
        "count": len(rows),
        "rows": rows,
        "accounts": [row["account_label"] for row in rows],
    }



def summarize_account_retention_templates(*, user) -> dict:
    templates = list(AccountRetentionPolicyTemplate.objects.filter(user=user).order_by("family_label", "template_name"))
    rows = []
    family_counts: dict[str, int] = {}
    for item in templates:
        active_fields = []
        for field_name, label in RETENTION_POLICY_FIELDS:
            value = getattr(item, field_name, None)
            if value:
                active_fields.append({"label": label, "days": value})
        family_label = (item.family_label or "").strip() or "General"
        family_counts[family_label] = family_counts.get(family_label, 0) + 1
        rows.append({
            "template": item,
            "template_name": item.template_name,
            "family_label": family_label,
            "active_fields": active_fields,
            "active_count": len(active_fields),
            "longest_days": max((field["days"] for field in active_fields), default=None),
            "shortest_days": min((field["days"] for field in active_fields), default=None),
        })
    family_rows = [
        {"family_label": family_label, "template_count": count}
        for family_label, count in sorted(family_counts.items(), key=lambda item: (item[0].lower(), item[1]))
    ]
    return {
        "count": len(rows),
        "rows": rows,
        "family_rows": family_rows,
    }



def _retention_signature(obj) -> tuple:
    return (
        getattr(obj, "evidence_retention_default_days", None),
        getattr(obj, "evidence_retention_verified_days", None),
        getattr(obj, "evidence_retention_strong_days", None),
        getattr(obj, "evidence_retention_weak_days", None),
        getattr(obj, "evidence_retention_placeholder_days", None),
        getattr(obj, "evidence_retention_confirmation_days", None),
        getattr(obj, "evidence_retention_import_match_days", None),
    )


def _active_account_labels_for_retention(user) -> list[str]:
    account_labels = set(
        str(value).strip()
        for value in HeldPosition.objects.filter(user=user).exclude(account_label="").values_list("account_label", flat=True)
        if str(value).strip()
    )
    account_labels.update(
        str(value).strip()
        for value in ImportedBrokerSnapshot.objects.filter(user=user).exclude(account_label="").values_list("account_label", flat=True)
        if str(value).strip()
    )
    account_labels.update(
        str(value).strip()
        for value in BrokerPositionImportRun.objects.filter(user=user).exclude(account_label="").values_list("account_label", flat=True)
        if str(value).strip()
    )
    return sorted(account_labels, key=lambda value: value.casefold())



def summarize_account_retention_template_recommendations(*, user) -> dict:
    templates = list(AccountRetentionPolicyTemplate.objects.filter(user=user).order_by("family_label", "template_name"))
    overrides = list(AccountRetentionPolicyOverride.objects.filter(user=user).order_by("account_label"))
    active_accounts = _active_account_labels_for_retention(user)

    template_signature_map: dict[tuple, list] = {}
    for template in templates:
        template_signature_map.setdefault(_retention_signature(template), []).append(template)

    override_signature_map: dict[tuple, list] = {}
    override_map = {}
    for override in overrides:
        sig = _retention_signature(override)
        override_signature_map.setdefault(sig, []).append(override)
        override_map[str(override.account_label or '').strip().casefold()] = override

    template_rows = []
    for template in templates:
        family_label = (template.family_label or '').strip() or 'General'
        family_key = family_label.casefold()
        suggested_accounts = []
        applied_count = 0
        matching_override_count = 0
        for account_label in active_accounts:
            account_key = account_label.casefold()
            override = override_map.get(account_key)
            if override and _retention_signature(override) == _retention_signature(template):
                matching_override_count += 1
            if family_key != 'general' and (family_key in account_key or account_key.startswith(family_key)):
                if override is None:
                    suggested_accounts.append(account_label)
                else:
                    applied_count += 1
        template_rows.append({
            'template': template,
            'family_label': family_label,
            'suggested_accounts': suggested_accounts[:6],
            'suggested_account_count': len(suggested_accounts),
            'suggested_account_extra_count': max(len(suggested_accounts) - 6, 0),
            'applied_count': applied_count,
            'matching_override_count': matching_override_count,
            'recommend_apply': bool(suggested_accounts),
        })

    uncovered_accounts = []
    for account_label in active_accounts:
        account_key = account_label.casefold()
        if account_key in override_map:
            continue
        recommended_template = None
        best_score = 0
        for template in templates:
            family_label = (template.family_label or '').strip()
            if not family_label:
                continue
            family_key = family_label.casefold()
            score = 0
            if family_key == account_key:
                score = 100
            elif family_key in account_key:
                score = 80 + len(family_key)
            elif account_key.startswith(family_key):
                score = 70 + len(family_key)
            else:
                shared = set(family_key.replace('-', ' ').replace('_', ' ').split()) & set(account_key.replace('-', ' ').replace('_', ' ').split())
                if shared:
                    score = 40 + len(shared)
            if score > best_score:
                best_score = score
                recommended_template = template
        uncovered_accounts.append({
            'account_label': account_label,
            'recommended_template': recommended_template,
            'match_score': best_score,
        })

    template_candidate_rows = []
    for signature, matched_overrides in override_signature_map.items():
        if signature in template_signature_map:
            continue
        if len(matched_overrides) < 2:
            continue
        source_override = matched_overrides[0]
        template_candidate_rows.append({
            'source_override': source_override,
            'account_labels': [item.account_label for item in matched_overrides],
            'count': len(matched_overrides),
        })

    template_candidate_rows.sort(key=lambda row: (-row['count'], row['source_override'].account_label.casefold()))
    template_rows.sort(key=lambda row: (-row['suggested_account_count'], -row['matching_override_count'], row['template'].template_name.casefold()))
    uncovered_accounts.sort(key=lambda row: (-bool(row['recommended_template']), -row['match_score'], row['account_label'].casefold()))

    return {
        'active_account_count': len(active_accounts),
        'template_rows': template_rows[:8],
        'uncovered_accounts': uncovered_accounts[:8],
        'template_candidate_rows': template_candidate_rows[:6],
        'recommended_apply_count': sum(1 for row in template_rows if row['recommend_apply']),
        'uncovered_count': len(uncovered_accounts),
        'template_candidate_count': len(template_candidate_rows),
    }

def summarize_account_retention_template_drift(*, user) -> dict:
    overrides = list(
        AccountRetentionPolicyOverride.objects.filter(user=user, source_template__isnull=False)
        .select_related("source_template")
        .order_by("account_label")
    )
    now = timezone.now()
    rows = []
    aligned_count = 0
    drifted_count = 0
    detached_candidate_count = 0

    for override in overrides:
        template = override.source_template
        changed_fields = []
        for field_name, label in RETENTION_POLICY_FIELDS:
            override_value = getattr(override, field_name, None)
            template_value = getattr(template, field_name, None)
            if override_value != template_value:
                changed_fields.append({
                    "label": label,
                    "override_days": override_value,
                    "template_days": template_value,
                })

        tx_qs = HoldingTransaction.objects.filter(position__user=user, account_label_snapshot__iexact=override.account_label)
        attachment_count = tx_qs.exclude(execution_evidence_attachment="").count()
        expiring_soon_count = tx_qs.filter(
            execution_evidence_retention_until__isnull=False,
            execution_evidence_retention_until__gte=now,
            execution_evidence_retention_until__lt=now + timedelta(days=30),
        ).count()
        expired_count = tx_qs.filter(execution_evidence_retention_until__lt=now).count()

        if changed_fields:
            drifted_count += 1
            posture = "DRIFTED"
        else:
            aligned_count += 1
            posture = "ALIGNED"

        if not attachment_count and changed_fields:
            detached_candidate_count += 1

        rows.append({
            "override": override,
            "account_label": override.account_label,
            "template": template,
            "template_name": template.template_name if template else "—",
            "family_label": (template.family_label or "").strip() if template else "",
            "changed_fields": changed_fields,
            "changed_field_count": len(changed_fields),
            "posture": posture,
            "attachment_count": attachment_count,
            "expiring_soon_count": expiring_soon_count,
            "expired_count": expired_count,
            "can_reset": bool(template),
            "can_detach": bool(template),
        })

    rows.sort(key=lambda row: (-row["changed_field_count"], row["account_label"].casefold()))
    top_drifted = next((row for row in rows if row["changed_field_count"]), None)
    return {
        "count": len(rows),
        "aligned_count": aligned_count,
        "drifted_count": drifted_count,
        "detached_candidate_count": detached_candidate_count,
        "rows": rows,
        "top_drifted": top_drifted,
    }


def summarize_account_retention_override_posture(*, user) -> dict:
    risk_profile = UserRiskProfile.objects.filter(user=user).first()
    override_rows = summarize_account_retention_overrides(user=user).get("rows", [])
    override_map = {str(row.get("account_label") or "").strip().casefold(): row for row in override_rows if str(row.get("account_label") or "").strip()}

    account_labels = set(
        HeldPosition.objects.filter(user=user).exclude(account_label="").values_list("account_label", flat=True)
    )
    account_labels.update(
        ImportedBrokerSnapshot.objects.filter(user=user).exclude(account_label="").values_list("account_label", flat=True)
    )
    account_labels.update(
        BrokerPositionImportRun.objects.filter(user=user).exclude(account_label="").values_list("account_label", flat=True)
    )

    def _global_fields() -> list[dict]:
        if not risk_profile:
            return []
        rows = []
        for field_name, label in RETENTION_POLICY_FIELDS:
            value = getattr(risk_profile, field_name, None)
            if value:
                rows.append({"label": label, "days": value})
        return rows

    global_fields = _global_fields()
    per_account_rows = []
    custom_count = 0
    global_count = 0
    for account_label in sorted([label for label in account_labels if str(label).strip()], key=lambda value: value.lower()):
        key = account_label.strip().casefold()
        override = override_map.get(key)
        active_fields = override.get("active_fields", []) if override else global_fields
        longest_days = max((field["days"] for field in active_fields), default=None)
        shortest_days = min((field["days"] for field in active_fields), default=None)
        row = {
            "account_label": account_label,
            "uses_override": bool(override),
            "source_label": "Custom override" if override else "Global preset",
            "active_fields": active_fields,
            "active_count": len(active_fields),
            "longest_days": longest_days,
            "shortest_days": shortest_days,
            "range_label": f"{shortest_days}–{longest_days}d" if shortest_days and longest_days else (f"{longest_days}d" if longest_days else "—"),
        }
        per_account_rows.append(row)
        if override:
            custom_count += 1
        else:
            global_count += 1

    most_detailed = max(
        per_account_rows,
        key=lambda row: (row["active_count"], row["longest_days"] or 0, row["account_label"].lower()),
        default=None,
    )

    return {
        "count": len(per_account_rows),
        "custom_count": custom_count,
        "global_count": global_count,
        "rows": per_account_rows,
        "most_detailed": most_detailed,
        "global_fields": global_fields,
        "global_range_label": (
            f"{min((field['days'] for field in global_fields), default=None)}–{max((field['days'] for field in global_fields), default=None)}d"
            if global_fields else "—"
        ),
    }


def summarize_stop_policy_followup_queue(*, user, account_label: str = "", status_filter: str = "ACTIONABLE", event_filter: str = "", symbol_filter: str = "", reason_filter: str = "", evidence_filter: str = "", evidence_type_filter: str = "", evidence_quality_filter: str = "", retention_filter: str = "", limit: int = 100) -> dict:
    account_label = (account_label or "").strip()
    status_filter = (status_filter or "ACTIONABLE").strip().upper()
    event_filter = (event_filter or "").strip().upper()
    symbol_filter = (symbol_filter or "").strip().upper()
    reason_filter = (reason_filter or "").strip().upper()
    evidence_filter = (evidence_filter or "").strip().upper()
    evidence_type_filter = (evidence_type_filter or "").strip().upper()
    evidence_quality_filter = (evidence_quality_filter or "").strip().upper()
    retention_filter = (retention_filter or "").strip().upper()
    now = timezone.now()

    tx_qs = HoldingTransaction.objects.select_related("position", "position__instrument").filter(
        position__user=user,
        event_type__in={HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD},
    )
    if account_label == "__UNLABELED__":
        tx_qs = tx_qs.filter(account_label_snapshot="")
    elif account_label:
        tx_qs = tx_qs.filter(account_label_snapshot__iexact=account_label)
    if event_filter in {HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD}:
        tx_qs = tx_qs.filter(event_type=event_filter)

    snapshot_candidates = list(ImportedBrokerSnapshot.objects.filter(user=user).order_by("-as_of", "-id")[:50])
    run_candidates = list(BrokerPositionImportRun.objects.filter(user=user).order_by("-created_at", "-id")[:50])
    resolution_candidates = list(BrokerPositionImportResolution.objects.select_related("run").filter(user=user).order_by("-resolved_at", "-id")[:100])

    rows = []
    for tx in tx_qs.order_by("-created_at", "-id")[:300]:
        derived = _derive_stop_policy_followup_bucket(tx, now=now)
        bucket = derived["bucket"]
        include = False
        if status_filter in {"", "ALL"}:
            include = True
        elif status_filter == "ACTIONABLE":
            include = derived["actionable"]
        elif status_filter == "PENDING":
            include = bucket in {"PENDING_ACTIVE", "PENDING_OVERDUE"}
        elif status_filter == "OVERDUE":
            include = bucket == "PENDING_OVERDUE"
        elif status_filter == "LATE":
            include = bucket == "LATE_RESOLVED"
        elif status_filter == "ON_TIME":
            include = bucket == "ON_TIME"
        if not include:
            continue

        reason_code = (tx.stop_policy_reason_code or "").strip()
        normalized_reason = reason_code.upper() if reason_code else "__UNSPECIFIED__"
        if reason_filter and normalized_reason != reason_filter:
            continue

        has_execution_evidence = _has_execution_evidence(tx)
        if evidence_filter == "WITH_EVIDENCE" and not has_execution_evidence:
            continue
        if evidence_filter == "WITHOUT_EVIDENCE" and has_execution_evidence:
            continue
        normalized_evidence_type = ((tx.execution_evidence_type or "").strip().upper() or "__NONE__")
        if evidence_type_filter and normalized_evidence_type != evidence_type_filter:
            continue
        normalized_evidence_quality = ((tx.execution_evidence_quality or "").strip().upper() or "__UNRATED__")
        if evidence_quality_filter and normalized_evidence_quality != evidence_quality_filter:
            continue

        has_attachment = bool(getattr(tx, "execution_evidence_attachment", None))
        retention_until = tx.execution_evidence_retention_until
        retention_expired = bool(retention_until and retention_until <= now)
        retention_expiring_soon = bool(retention_until and now < retention_until <= (now + timedelta(days=30)))
        retention_missing = bool(has_attachment and not retention_until)
        if retention_filter == "ATTACHMENT" and not has_attachment:
            continue
        if retention_filter == "TEXT_ONLY" and (not has_execution_evidence or has_attachment):
            continue
        if retention_filter == "EXPIRING_SOON" and not retention_expiring_soon:
            continue
        if retention_filter == "EXPIRED" and not retention_expired:
            continue
        if retention_filter == "MISSING_RETENTION" and not retention_missing:
            continue

        position = tx.position
        holding_open = position.status == HeldPosition.Status.OPEN
        account_display = (tx.account_label_snapshot or "").strip() or "Unlabeled / blended"
        symbol_upper = position.instrument.symbol.upper()
        account_key = (tx.account_label_snapshot or tx.position.account_label or "").strip().casefold()
        row_snapshots = [
            snap for snap in snapshot_candidates
            if (not account_key or not (snap.account_label or "").strip() or (snap.account_label or "").strip().casefold() == account_key)
        ][:5]
        row_runs = [
            run for run in run_candidates
            if (not account_key or not (run.account_label or "").strip() or (run.account_label or "").strip().casefold() == account_key)
        ][:5]
        row_resolutions = [
            res for res in resolution_candidates
            if res.symbol.upper() == symbol_upper and (not account_key or not ((res.run.account_label if res.run else "") or "").strip() or ((res.run.account_label if res.run else "") or "").strip().casefold() == account_key)
        ][:5]

        rows.append({
            "tx": tx,
            "position": position,
            "symbol": position.instrument.symbol,
            "account_label": account_display,
            "bucket": bucket,
            "bucket_label": derived["bucket_label"],
            "status": derived["status"],
            "hours_open": derived["hours_open"],
            "age_label": _format_hours_to_resolution(derived["hours_open"]),
            "due_at": tx.stop_policy_due_at,
            "resolved_at": derived["resolved_at"],
            "resolution_label": _format_hours_to_resolution(max(0.0, (tx.stop_policy_resolved_at - tx.created_at).total_seconds() / 3600)) if tx.stop_policy_resolved_at and tx.created_at else "—",
            "needs_action": derived["actionable"] and holding_open,
            "followup_label": "Record / tighten stop" if holding_open and derived["actionable"] else "Review history",
            "reason_code": reason_code,
            "reason_label": tx.get_stop_policy_reason_code_display() if reason_code else "",
            "reason_note": (tx.stop_policy_note or "").strip(),
            "has_execution_evidence": has_execution_evidence,
            "execution_evidence_type": (tx.execution_evidence_type or "").strip(),
            "execution_evidence_label": _execution_evidence_label(tx),
            "execution_evidence_quality": (tx.execution_evidence_quality or "").strip(),
            "execution_evidence_quality_label": _execution_evidence_quality_label(tx),
            "execution_evidence_quality_rank": _execution_evidence_quality_rank(tx),
            "has_execution_evidence_attachment": has_attachment,
            "execution_evidence_reference": (tx.execution_evidence_reference or "").strip(),
            "execution_evidence_note": (tx.execution_evidence_note or "").strip(),
            "execution_evidence_recorded_at": tx.execution_evidence_recorded_at,
            "execution_evidence_attachment": tx.execution_evidence_attachment,
            "execution_evidence_attachment_name": (tx.execution_evidence_attachment.name.split("/")[-1] if getattr(tx, "execution_evidence_attachment", None) else ""),
            "execution_evidence_attachment_url": (tx.execution_evidence_attachment.url if getattr(tx, "execution_evidence_attachment", None) else ""),
            "execution_evidence_retention_until": retention_until,
            "attachment_retention_expired": retention_expired,
            "attachment_retention_expiring_soon": retention_expiring_soon,
            "attachment_retention_missing": retention_missing,
            "retention_bucket": "EXPIRED" if retention_expired else "EXPIRING_SOON" if retention_expiring_soon else "MISSING_RETENTION" if retention_missing else "ACTIVE" if has_attachment else "TEXT_ONLY" if has_execution_evidence else "NONE",
            "broker_confirmation_snapshot_id": tx.broker_confirmation_snapshot_id,
            "broker_confirmation_run_id": tx.broker_confirmation_run_id,
            "broker_confirmation_resolution_id": tx.broker_confirmation_resolution_id,
            "broker_confirmation_linked": bool(tx.broker_confirmation_snapshot_id or tx.broker_confirmation_run_id or tx.broker_confirmation_resolution_id),
            "broker_confirmation_linked_at": tx.broker_confirmation_linked_at,
            "broker_confirmation_snapshot_options": [{"id": item.id, "label": f"{(item.account_label or item.source_label or 'Broker snapshot')} · {item.as_of:%Y-%m-%d %H:%M}"} for item in row_snapshots],
            "broker_confirmation_run_options": [{"id": item.id, "label": f"{(item.account_label or item.uploaded_filename or item.source_label or 'Broker run')} · {item.created_at:%Y-%m-%d %H:%M}"} for item in row_runs],
            "broker_confirmation_resolution_options": [{"id": item.id, "label": f"{item.symbol} · {item.get_action_display()} · {item.resolved_at:%Y-%m-%d %H:%M}"} for item in row_resolutions],
            "broker_confirmation_snapshot_label": (f"{(tx.broker_confirmation_snapshot.account_label or tx.broker_confirmation_snapshot.source_label or 'Broker snapshot')} · {tx.broker_confirmation_snapshot.as_of:%Y-%m-%d %H:%M}" if getattr(tx, 'broker_confirmation_snapshot', None) else ""),
            "broker_confirmation_run_label": (f"{(tx.broker_confirmation_run.account_label or tx.broker_confirmation_run.uploaded_filename or tx.broker_confirmation_run.source_label or 'Broker run')} · {tx.broker_confirmation_run.created_at:%Y-%m-%d %H:%M}" if getattr(tx, 'broker_confirmation_run', None) else ""),
            "broker_confirmation_resolution_label": (f"{tx.broker_confirmation_resolution.symbol} · {tx.broker_confirmation_resolution.get_action_display()} · {tx.broker_confirmation_resolution.resolved_at:%Y-%m-%d %H:%M}" if getattr(tx, 'broker_confirmation_resolution', None) else ""),
        })

    rows.sort(key=lambda row: (
        0 if row["bucket"] == "PENDING_OVERDUE" else 1 if row["bucket"] == "PENDING_ACTIVE" else 2 if row["bucket"] == "LATE_RESOLVED" else 3,
        -(row["hours_open"] or 0),
        row["symbol"],
    ))

    account_counts = {}
    for row in rows:
        acct = row["account_label"]
        bucket = account_counts.setdefault(acct, {"count": 0, "pending": 0, "overdue": 0, "late": 0, "on_time": 0})
        bucket["count"] += 1
        if row["bucket"] == "PENDING_OVERDUE":
            bucket["overdue"] += 1
        elif row["bucket"] == "PENDING_ACTIVE":
            bucket["pending"] += 1
        elif row["bucket"] == "LATE_RESOLVED":
            bucket["late"] += 1
        elif row["bucket"] == "ON_TIME":
            bucket["on_time"] += 1

    account_rows = [
        {"account_label": acct, **counts}
        for acct, counts in account_counts.items()
    ]

    reason_counts = {}
    for row in rows:
        if not row["reason_code"]:
            continue
        bucket = reason_counts.setdefault(row["reason_code"], {"label": row["reason_label"], "count": 0})
        bucket["count"] += 1
    account_rows.sort(key=lambda row: (-row["overdue"], -row["pending"], -row["late"], row["account_label"].lower()))

    all_transactions = list(tx_qs.order_by("-created_at", "-id")[:300])
    summary = {"overdue": 0, "pending": 0, "late": 0, "on_time": 0}
    for tx in all_transactions:
        bucket = _derive_stop_policy_followup_bucket(tx, now=now)["bucket"]
        if bucket == "PENDING_OVERDUE":
            summary["overdue"] += 1
        elif bucket == "PENDING_ACTIVE":
            summary["pending"] += 1
        elif bucket == "LATE_RESOLVED":
            summary["late"] += 1
        elif bucket == "ON_TIME":
            summary["on_time"] += 1

    reason_rows = sorted(reason_counts.values(), key=lambda row: (-row["count"], row["label"].lower()))

    attachment_rows = [row for row in rows if row["has_execution_evidence_attachment"]]
    retention_summary = {
        "attachment_count": len(attachment_rows),
        "text_only_count": sum(1 for row in rows if row["has_execution_evidence"] and not row["has_execution_evidence_attachment"]),
        "active_count": sum(1 for row in attachment_rows if not row["attachment_retention_expired"] and not row["attachment_retention_expiring_soon"] and not row["attachment_retention_missing"]),
        "expiring_soon_count": sum(1 for row in attachment_rows if row["attachment_retention_expiring_soon"]),
        "expired_count": sum(1 for row in attachment_rows if row["attachment_retention_expired"]),
        "missing_retention_count": sum(1 for row in attachment_rows if row["attachment_retention_missing"]),
    }

    return {
        "rows": rows[: max(1, limit)],
        "count": len(rows),
        "account_rows": account_rows[:12],
        "summary": summary,
        "reason_rows": reason_rows[:8],
        "retention_summary": retention_summary,
        "noted_count": sum(1 for row in rows if row["reason_code"] or row["reason_note"]),
        "status_filter": status_filter or "ACTIONABLE",
        "event_filter": event_filter,
        "symbol_filter": symbol_filter,
        "reason_filter": reason_filter,
        "evidence_filter": evidence_filter,
        "evidence_type_filter": evidence_type_filter,
        "evidence_quality_filter": evidence_quality_filter,
        "retention_filter": retention_filter,
        "with_evidence_count": sum(1 for row in rows if row["has_execution_evidence"]),
        "without_evidence_count": sum(1 for row in rows if not row["has_execution_evidence"]),
    }


def _build_stop_discipline_period_stats(transactions: list[HoldingTransaction]) -> dict:
    posture_counts = {"OVER": 0, "NEAR": 0, "OK": 0, "UNRECORDED": 0}
    missing_stop_events = 0
    for tx in transactions:
        posture = (tx.risk_guardrail_posture_snapshot or "").strip().upper() or "UNRECORDED"
        posture_counts[posture] = posture_counts.get(posture, 0) + 1
        if tx.stop_price_snapshot is None and tx.event_type in {HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD}:
            missing_stop_events += 1
    recorded_total = posture_counts.get("OVER", 0) + posture_counts.get("NEAR", 0) + posture_counts.get("OK", 0)
    hygiene_score = None
    pressure_score = None
    if recorded_total > 0:
        hygiene_score = round((posture_counts.get("OK", 0) / recorded_total) * 100, 1)
        pressure_score = round(((posture_counts.get("OVER", 0) + posture_counts.get("NEAR", 0)) / recorded_total) * 100, 1)
    debt_events = posture_counts.get("OVER", 0) + missing_stop_events
    debt_rate = round((debt_events / len(transactions)) * 100, 1) if transactions else None
    return {
        "count": len(transactions),
        "posture_counts": posture_counts,
        "missing_stop_events": missing_stop_events,
        "recorded_total": recorded_total,
        "hygiene_score": hygiene_score,
        "pressure_score": pressure_score,
        "debt_events": debt_events,
        "debt_rate": debt_rate,
    }


def summarize_stop_discipline_trends(*, user, account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    tx_qs = HoldingTransaction.objects.select_related("position", "position__instrument").filter(
        position__user=user,
        event_type__in={
            HoldingTransaction.EventType.OPEN,
            HoldingTransaction.EventType.BUY_ADD,
            HoldingTransaction.EventType.PARTIAL_SELL,
            HoldingTransaction.EventType.CLOSE,
        },
    )
    if account_label == "__UNLABELED__":
        tx_qs = tx_qs.filter(account_label_snapshot="")
    elif account_label:
        tx_qs = tx_qs.filter(account_label_snapshot__iexact=account_label)
    transactions = list(tx_qs.order_by("-created_at", "-id")[:250])

    now = timezone.now()
    recent_7_cutoff = now - timedelta(days=7)
    recent_30_cutoff = now - timedelta(days=30)
    recent_90_cutoff = now - timedelta(days=90)
    prior_30_start = now - timedelta(days=60)

    recent_7 = [tx for tx in transactions if tx.created_at >= recent_7_cutoff]
    recent_30 = [tx for tx in transactions if tx.created_at >= recent_30_cutoff]
    recent_90 = [tx for tx in transactions if tx.created_at >= recent_90_cutoff]
    prior_30 = [tx for tx in transactions if prior_30_start <= tx.created_at < recent_30_cutoff]

    recent_7_stats = _build_stop_discipline_period_stats(recent_7)
    recent_30_stats = _build_stop_discipline_period_stats(recent_30)
    recent_90_stats = _build_stop_discipline_period_stats(recent_90)
    prior_30_stats = _build_stop_discipline_period_stats(prior_30)

    trend_direction = "FLAT"
    trend_label = "Stable"
    trend_reason = "Recent stop-discipline execution is broadly in line with the prior period."
    hygiene_delta = None
    debt_delta = None
    if recent_30_stats["hygiene_score"] is not None and prior_30_stats["hygiene_score"] is not None:
        hygiene_delta = round(recent_30_stats["hygiene_score"] - prior_30_stats["hygiene_score"], 1)
    if recent_30_stats["debt_rate"] is not None and prior_30_stats["debt_rate"] is not None:
        debt_delta = round(recent_30_stats["debt_rate"] - prior_30_stats["debt_rate"], 1)

    if hygiene_delta is None and debt_delta is None:
        trend_direction = "NEW" if recent_30_stats["count"] else "NO_DATA"
        trend_label = "Not enough history" if recent_30_stats["count"] else "No recent events"
        trend_reason = "The app needs both a recent and prior stop-discipline window before it can call the trend improving or degrading."
    else:
        hygiene_delta = hygiene_delta or 0.0
        debt_delta = debt_delta or 0.0
        if hygiene_delta >= 5 or debt_delta <= -10:
            trend_direction = "IMPROVING"
            trend_label = "Improving"
            trend_reason = "Recent stop-discipline execution shows fewer over-limit / missing-stop events than the prior 30-day window."
        elif hygiene_delta <= -5 or debt_delta >= 10:
            trend_direction = "DEGRADING"
            trend_label = "Degrading"
            trend_reason = "Recent stop-discipline execution shows more over-limit or missing-stop debt than the prior 30-day window."

    buckets: dict[str, list[HoldingTransaction]] = {}
    for tx in recent_30:
        label = (tx.account_label_snapshot or "").strip() or "Unlabeled / blended"
        buckets.setdefault(label, []).append(tx)
    account_rows = []
    for label, bucket in buckets.items():
        stats = _build_stop_discipline_period_stats(bucket)
        overall = "OVER" if stats["debt_events"] else ("NEAR" if stats["posture_counts"].get("NEAR", 0) else "OK")
        latest = bucket[0] if bucket else None
        account_rows.append({
            "account_label": label,
            "count": stats["count"],
            "hygiene_score": stats["hygiene_score"],
            "debt_rate": stats["debt_rate"],
            "over_count": stats["posture_counts"].get("OVER", 0),
            "near_count": stats["posture_counts"].get("NEAR", 0),
            "missing_stop_events": stats["missing_stop_events"],
            "overall_posture": overall,
            "latest": latest,
        })
    account_rows.sort(key=lambda row: (0 if row["overall_posture"] == "OVER" else 1 if row["overall_posture"] == "NEAR" else 2, -(row["debt_rate"] or 0), row["account_label"].lower()))

    def _period_row(label: str, stats: dict, days: int) -> dict:
        return {
            "label": label,
            "days": days,
            "count": stats["count"],
            "hygiene_score": stats["hygiene_score"],
            "debt_rate": stats["debt_rate"],
            "over_count": stats["posture_counts"].get("OVER", 0),
            "near_count": stats["posture_counts"].get("NEAR", 0),
            "ok_count": stats["posture_counts"].get("OK", 0),
            "missing_stop_events": stats["missing_stop_events"],
        }

    return {
        "recent_7": recent_7_stats,
        "recent_30": recent_30_stats,
        "recent_90": recent_90_stats,
        "prior_30": prior_30_stats,
        "trend_direction": trend_direction,
        "trend_label": trend_label,
        "trend_reason": trend_reason,
        "hygiene_delta": hygiene_delta,
        "debt_rate_delta": debt_delta,
        "period_rows": [
            _period_row("7d", recent_7_stats, 7),
            _period_row("30d", recent_30_stats, 30),
            _period_row("90d", recent_90_stats, 90),
        ],
        "account_rows": account_rows[:8],
    }


def summarize_stop_discipline_history(*, user, account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    tx_qs = HoldingTransaction.objects.select_related("position", "position__instrument").filter(position__user=user)
    if account_label == "__UNLABELED__":
        tx_qs = tx_qs.filter(account_label_snapshot="")
    elif account_label:
        tx_qs = tx_qs.filter(account_label_snapshot__iexact=account_label)
    transactions = list(tx_qs.order_by("-created_at", "-id")[:50])
    discipline_events = [
        tx for tx in transactions
        if tx.event_type in {
            HoldingTransaction.EventType.OPEN,
            HoldingTransaction.EventType.BUY_ADD,
            HoldingTransaction.EventType.PARTIAL_SELL,
            HoldingTransaction.EventType.CLOSE,
        }
    ]
    posture_counts = {"OVER": 0, "NEAR": 0, "OK": 0, "UNRECORDED": 0}
    for tx in discipline_events:
        posture = (tx.risk_guardrail_posture_snapshot or "").strip().upper() or "UNRECORDED"
        posture_counts[posture] = posture_counts.get(posture, 0) + 1
    rows = []
    for tx in discipline_events[:12]:
        posture = (tx.risk_guardrail_posture_snapshot or "").strip().upper() or "UNRECORDED"
        rows.append({
            "tx": tx,
            "account_label": (tx.account_label_snapshot or "").strip() or "Unlabeled / blended",
            "posture": posture,
            "has_stop": tx.stop_price_snapshot is not None,
        })
    account_rows = []
    buckets: dict[str, list[HoldingTransaction]] = {}
    for tx in discipline_events:
        label = (tx.account_label_snapshot or "").strip() or "Unlabeled / blended"
        buckets.setdefault(label, []).append(tx)
    for label, bucket in buckets.items():
        counts = {"OVER": 0, "NEAR": 0, "OK": 0, "UNRECORDED": 0}
        missing_stop_events = 0
        for tx in bucket:
            posture = (tx.risk_guardrail_posture_snapshot or "").strip().upper() or "UNRECORDED"
            counts[posture] = counts.get(posture, 0) + 1
            if tx.stop_price_snapshot is None and tx.event_type in {HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD}:
                missing_stop_events += 1
        last_tx = bucket[0] if bucket else None
        account_rows.append({
            "account_label": label,
            "event_count": len(bucket),
            "missing_stop_events": missing_stop_events,
            "over_count": counts["OVER"],
            "near_count": counts["NEAR"],
            "ok_count": counts["OK"],
            "last_tx": last_tx,
        })
    account_rows.sort(key=lambda row: (-row["over_count"], -row["near_count"], row["account_label"].lower()))
    return {
        "count": len(discipline_events),
        "posture_counts": posture_counts,
        "missing_stop_events": sum(1 for tx in discipline_events if tx.stop_price_snapshot is None and tx.event_type in {HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD}),
        "rows": rows,
        "account_rows": account_rows,
    }


def summarize_open_holdings(*, user=None, account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    qs = HeldPosition.objects.select_related("instrument").filter(status=HeldPosition.Status.OPEN)
    if user is not None:
        qs = qs.filter(user=user)
    if account_label == "__UNLABELED__":
        qs = qs.filter(account_label="")
    elif account_label:
        qs = qs.filter(account_label__iexact=account_label)
    snapshots = [build_holding_health_snapshot(item) for item in qs]
    ordered = sorted(snapshots, key=lambda item: (-item.recommendation_rank, item.position.instrument.symbol))
    return {
        "total_open": len(snapshots),
        "thesis_broken": sum(1 for item in snapshots if item.thesis_broken),
        "stop_breached": sum(1 for item in snapshots if item.stop_breached),
        "deteriorating": sum(1 for item in snapshots if item.deteriorating),
        "target_reached": sum(1 for item in snapshots if item.target_reached),
        "missing_from_latest_import": (HeldPosition.objects.filter(user=user, status=HeldPosition.Status.OPEN, missing_from_latest_import=True).filter(account_label__iexact=account_label).count() if user is not None and account_label else (HeldPosition.objects.filter(user=user, status=HeldPosition.Status.OPEN, missing_from_latest_import=True).count() if user is not None else 0)),
        "sell_now": sum(1 for item in snapshots if item.recommendation_code == "SELL_NOW"),
        "review_now": sum(1 for item in snapshots if item.recommendation_code in {"REVIEW_URGENT", "REVIEW"}),
        "trim_or_exit": sum(1 for item in snapshots if item.recommendation_code == "TRIM_OR_EXIT"),
        "urgent_snapshots": ordered[:5],
        "snapshots": ordered[:10],
    }



@dataclass(frozen=True)
class PortfolioExposureItem:
    position: HeldPosition
    market_value: Decimal
    weight_pct: Decimal | None
    price_used: Decimal | None


def summarize_portfolio_exposure(*, user=None, account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    qs = HeldPosition.objects.select_related("instrument").filter(status=HeldPosition.Status.OPEN)
    if user is not None:
        qs = qs.filter(user=user)
    if account_label == "__UNLABELED__":
        qs = qs.filter(account_label="")
    elif account_label:
        qs = qs.filter(account_label__iexact=account_label)
    positions = list(qs)
    items: list[PortfolioExposureItem] = []
    total_market_value = Decimal("0")
    total_cost_basis = Decimal("0")
    for position in positions:
        refreshed = refresh_position_market_state(position)
        price_used = Decimal(refreshed.last_price) if refreshed.last_price is not None else Decimal(refreshed.average_entry_price)
        market_value = (Decimal(refreshed.quantity) * price_used).quantize(Decimal("0.01"))
        total_market_value += market_value
        total_cost_basis += (Decimal(refreshed.quantity) * Decimal(refreshed.average_entry_price)).quantize(Decimal("0.01"))
        items.append(PortfolioExposureItem(position=refreshed, market_value=market_value, weight_pct=None, price_used=price_used))

    ranked: list[PortfolioExposureItem] = []
    for item in items:
        weight_pct = None
        if total_market_value > 0:
            weight_pct = ((item.market_value / total_market_value) * Decimal("100")).quantize(Decimal("0.01"))
        ranked.append(PortfolioExposureItem(position=item.position, market_value=item.market_value, weight_pct=weight_pct, price_used=item.price_used))
    ranked.sort(key=lambda item: (item.weight_pct or Decimal("0")), reverse=True)

    account_equity = None
    cash_headroom = None
    gross_exposure_pct = None
    net_exposure_pct = None
    net_exposure_posture = None
    net_exposure_headroom_pct = None
    max_position_weight_pct = None
    max_sector_weight_pct = None
    concentration_warning_buffer_pct = None
    max_high_correlation_positions = None
    high_correlation_threshold = None
    correlation_lookback_bars = None
    max_net_exposure_pct = None
    net_exposure_warning_buffer_pct = None
    try:
        from .models import UserRiskProfile
        profile = UserRiskProfile.objects.filter(user=user).first() if user is not None else None
        if profile:
            max_position_weight_pct = Decimal(profile.max_position_weight_pct or 0).quantize(Decimal("0.01"))
            max_sector_weight_pct = Decimal(profile.max_sector_weight_pct or 0).quantize(Decimal("0.01"))
            concentration_warning_buffer_pct = Decimal(profile.concentration_warning_buffer_pct or 0).quantize(Decimal("0.01"))
            max_high_correlation_positions = int(profile.max_high_correlation_positions or 0)
            high_correlation_threshold = Decimal(profile.high_correlation_threshold or 0).quantize(Decimal("0.01"))
            correlation_lookback_bars = int(profile.correlation_lookback_bars or 0)
            max_net_exposure_pct = Decimal(profile.max_net_exposure_pct or 0).quantize(Decimal("0.01"))
            net_exposure_warning_buffer_pct = Decimal(profile.net_exposure_warning_buffer_pct or 0).quantize(Decimal("0.01"))
        if profile and profile.account_equity:
            account_equity = Decimal(profile.account_equity).quantize(Decimal("0.01"))
            cash_headroom = (account_equity - total_market_value).quantize(Decimal("0.01"))
            if account_equity > 0:
                gross_exposure_pct = ((total_market_value / account_equity) * Decimal("100")).quantize(Decimal("0.01"))
                net_exposure_pct = gross_exposure_pct
    except Exception:
        profile = None

    near_limit_floor = None
    if max_position_weight_pct is not None and concentration_warning_buffer_pct is not None:
        near_limit_floor = max(Decimal("0.00"), max_position_weight_pct - concentration_warning_buffer_pct)

    top_positions = []
    over_position_cap_count = 0
    near_position_cap_count = 0
    for item in ranked[:5]:
        posture = "OK"
        headroom_pct = None
        if item.weight_pct is not None and max_position_weight_pct is not None:
            headroom_pct = (max_position_weight_pct - item.weight_pct).quantize(Decimal("0.01"))
            if item.weight_pct > max_position_weight_pct:
                posture = "OVER"
                over_position_cap_count += 1
            elif near_limit_floor is not None and item.weight_pct >= near_limit_floor:
                posture = "NEAR"
                near_position_cap_count += 1
        top_positions.append({
            "position": item.position,
            "market_value": item.market_value,
            "weight_pct": item.weight_pct,
            "price_used": item.price_used,
            "cap_posture": posture,
            "cap_headroom_pct": headroom_pct,
        })

    for item in ranked[5:]:
        if item.weight_pct is None or max_position_weight_pct is None:
            continue
        if item.weight_pct > max_position_weight_pct:
            over_position_cap_count += 1
        elif near_limit_floor is not None and item.weight_pct >= near_limit_floor:
            near_position_cap_count += 1

    if net_exposure_pct is not None and max_net_exposure_pct is not None and max_net_exposure_pct > 0:
        net_exposure_headroom_pct = (max_net_exposure_pct - net_exposure_pct).quantize(Decimal("0.01"))
        net_near_floor = max(Decimal("0.00"), max_net_exposure_pct - (net_exposure_warning_buffer_pct or Decimal("0.00")))
        if net_exposure_pct > max_net_exposure_pct:
            net_exposure_posture = "OVER"
        elif net_exposure_pct >= net_near_floor:
            net_exposure_posture = "NEAR"
        else:
            net_exposure_posture = "OK"

    return {
        "open_count": len(ranked),
        "total_market_value": total_market_value.quantize(Decimal("0.01")),
        "total_cost_basis": total_cost_basis.quantize(Decimal("0.01")),
        "unrealized_pnl": (total_market_value - total_cost_basis).quantize(Decimal("0.01")),
        "account_equity": account_equity,
        "current_equity": (account_equity + (total_market_value - total_cost_basis)).quantize(Decimal("0.01")) if account_equity is not None else None,
        "cash_headroom": cash_headroom,
        "gross_exposure_pct": gross_exposure_pct,
        "net_exposure_pct": net_exposure_pct,
        "net_exposure_posture": net_exposure_posture,
        "net_exposure_headroom_pct": net_exposure_headroom_pct,
        "max_position_weight_pct": max_position_weight_pct,
        "max_sector_weight_pct": max_sector_weight_pct,
        "concentration_warning_buffer_pct": concentration_warning_buffer_pct,
        "max_high_correlation_positions": max_high_correlation_positions,
        "high_correlation_threshold": high_correlation_threshold,
        "correlation_lookback_bars": correlation_lookback_bars,
        "max_net_exposure_pct": max_net_exposure_pct,
        "net_exposure_warning_buffer_pct": net_exposure_warning_buffer_pct,
        "top_positions": top_positions,
        "over_position_cap_count": over_position_cap_count,
        "near_position_cap_count": near_position_cap_count,
        "weights": {item.position.id: item.weight_pct for item in ranked},
        "market_values": {item.position.id: item.market_value for item in ranked},
    }








def summarize_account_exposure_heatmap(*, user) -> dict:
    raw_labels = []
    raw_labels.extend(HeldPosition.objects.filter(user=user).values_list("account_label", flat=True))
    raw_labels.extend(ImportedBrokerSnapshot.objects.filter(user=user).values_list("account_label", flat=True))
    raw_labels.extend(BrokerPositionImportRun.objects.filter(user=user).values_list("account_label", flat=True))

    seen: set[str] = set()
    account_keys: list[str] = []
    saw_blank = False
    for value in raw_labels:
        normalized = (value or "").strip()
        if not normalized:
            saw_blank = True
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        account_keys.append(normalized)
    account_keys.sort(key=str.lower)
    if saw_blank:
        account_keys.insert(0, "__UNLABELED__")

    rows = []
    posture_counts = {"OVER": 0, "NEAR": 0, "OK": 0}
    hottest_sector = None
    heaviest_position = None

    for account_key in account_keys:
        account_label = "Unlabeled / blended" if account_key == "__UNLABELED__" else account_key
        exposure = summarize_portfolio_exposure(user=user, account_label=account_key)
        sector_exposure = summarize_holding_sector_exposure(user=user, account_label=account_key)
        top_position = exposure.get("top_positions", [None])[0] if exposure.get("top_positions") else None
        top_sector = sector_exposure.get("top_sector")
        over_count = int(exposure.get("over_position_cap_count") or 0) + int(sector_exposure.get("over_cap_count") or 0)
        near_count = int(exposure.get("near_position_cap_count") or 0) + int(sector_exposure.get("near_cap_count") or 0)
        if exposure.get("net_exposure_posture") == "OVER":
            over_count += 1
        elif exposure.get("net_exposure_posture") == "NEAR":
            near_count += 1

        if over_count > 0:
            overall_posture = "OVER"
        elif near_count > 0:
            overall_posture = "NEAR"
        else:
            overall_posture = "OK"
        posture_counts[overall_posture] = posture_counts.get(overall_posture, 0) + 1

        row = {
            "account_key": account_key,
            "account_label": account_label,
            "exposure": exposure,
            "sector_exposure": sector_exposure,
            "top_position": top_position,
            "top_sector": top_sector,
            "overall_posture": overall_posture,
            "over_count": over_count,
            "near_count": near_count,
            "crowding_score": (over_count * 2) + near_count,
        }
        rows.append(row)

        if top_sector and (hottest_sector is None or Decimal(top_sector.get("weight_pct") or 0) > Decimal(hottest_sector.get("top_sector", {}).get("weight_pct") or 0)):
            hottest_sector = row
        if top_position and (heaviest_position is None or Decimal(top_position.get("weight_pct") or 0) > Decimal((heaviest_position.get("top_position") or {}).get("weight_pct") or 0)):
            heaviest_position = row

    rows.sort(key=lambda row: (-row["crowding_score"], -(Decimal((row.get("top_sector") or {}).get("weight_pct") or 0)), -(Decimal((row.get("top_position") or {}).get("weight_pct") or 0)), row["account_label"].lower()))
    return {
        "rows": rows,
        "count": len(rows),
        "posture_counts": posture_counts,
        "hottest_sector_account": hottest_sector,
        "heaviest_position_account": heaviest_position,
    }


def summarize_account_drawdown_monitoring(*, user) -> dict:
    raw_labels = []
    raw_labels.extend(HeldPosition.objects.filter(user=user).values_list("account_label", flat=True))
    raw_labels.extend(ImportedBrokerSnapshot.objects.filter(user=user).values_list("account_label", flat=True))
    raw_labels.extend(BrokerPositionImportRun.objects.filter(user=user).values_list("account_label", flat=True))

    seen: set[str] = set()
    account_keys: list[str] = []
    saw_blank = False
    for value in raw_labels:
        normalized = (value or "").strip()
        if not normalized:
            saw_blank = True
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        account_keys.append(normalized)
    account_keys.sort(key=str.lower)
    if saw_blank:
        account_keys.insert(0, "__UNLABELED__")

    deterioration_limit = Decimal(str(abs(float(getattr(settings, "HELD_POSITION_DETERIORATION_ALERT_PCT", 5.0) or 5.0)))).quantize(Decimal("0.01"))
    review_limit = Decimal(str(abs(float(getattr(settings, "HELD_POSITION_REVIEW_WARNING_PCT", 2.5) or 2.5)))).quantize(Decimal("0.01"))

    rows = []
    posture_counts = {"STRESSED": 0, "WARNING": 0, "OK": 0, "NO_OPEN": 0}
    worst_account = None

    for account_key in account_keys:
        account_label = "Unlabeled / blended" if account_key == "__UNLABELED__" else account_key
        exposure = summarize_portfolio_exposure(user=user, account_label=account_key)
        summary = summarize_open_holdings(user=user, account_label=account_key)
        snapshots = summary.get("snapshots") or []
        urgent_snapshots = summary.get("urgent_snapshots") or []
        open_count = int(summary.get("total_open") or 0)
        warning_count = sum(1 for item in snapshots if item.warning_drawdown and not item.deteriorating)
        deep_count = sum(1 for item in snapshots if item.deteriorating)
        sell_now_count = sum(1 for item in snapshots if item.recommendation_code == "SELL_NOW")
        review_now_count = sum(1 for item in snapshots if item.recommendation_code in {"REVIEW_URGENT", "REVIEW"})
        worst_snapshot = None
        worst_drawdown_pct = None
        for item in snapshots:
            pnl_pct = item.position.pnl_pct
            if pnl_pct is None:
                continue
            pnl_decimal = Decimal(str(pnl_pct)).quantize(Decimal("0.01"))
            if worst_drawdown_pct is None or pnl_decimal < worst_drawdown_pct:
                worst_drawdown_pct = pnl_decimal
                worst_snapshot = item

        stressed_pct = None
        if open_count > 0:
            stressed_pct = ((Decimal(deep_count) / Decimal(open_count)) * Decimal("100")).quantize(Decimal("0.01"))

        if open_count == 0:
            overall_posture = "NO_OPEN"
        elif deep_count > 0 or sell_now_count > 0:
            overall_posture = "STRESSED"
        elif warning_count > 0 or review_now_count > 0:
            overall_posture = "WARNING"
        else:
            overall_posture = "OK"
        posture_counts[overall_posture] = posture_counts.get(overall_posture, 0) + 1

        row = {
            "account_key": account_key,
            "account_label": account_label,
            "exposure": exposure,
            "open_count": open_count,
            "warning_count": warning_count,
            "deep_count": deep_count,
            "sell_now_count": sell_now_count,
            "review_now_count": review_now_count,
            "worst_snapshot": worst_snapshot,
            "worst_drawdown_pct": worst_drawdown_pct,
            "stressed_pct": stressed_pct,
            "overall_posture": overall_posture,
            "review_limit_pct": review_limit,
            "deterioration_limit_pct": deterioration_limit,
            "urgent_snapshots": urgent_snapshots[:3],
        }
        rows.append(row)

        if worst_drawdown_pct is not None and (worst_account is None or worst_drawdown_pct < (worst_account.get("worst_drawdown_pct") if worst_account else Decimal("0.00"))):
            worst_account = row

    rows.sort(key=lambda row: (
        0 if row["overall_posture"] == "STRESSED" else 1 if row["overall_posture"] == "WARNING" else 2 if row["overall_posture"] == "OK" else 3,
        row.get("worst_drawdown_pct") if row.get("worst_drawdown_pct") is not None else Decimal("999.99"),
        row["account_label"].lower(),
    ))
    return {
        "rows": rows,
        "count": len(rows),
        "posture_counts": posture_counts,
        "worst_account": worst_account,
        "review_limit_pct": review_limit,
        "deterioration_limit_pct": deterioration_limit,
    }


def summarize_account_risk_posture(*, user) -> dict:
    raw_labels = []
    raw_labels.extend(HeldPosition.objects.filter(user=user).values_list("account_label", flat=True))
    raw_labels.extend(ImportedBrokerSnapshot.objects.filter(user=user).values_list("account_label", flat=True))
    raw_labels.extend(BrokerPositionImportRun.objects.filter(user=user).values_list("account_label", flat=True))

    seen: set[str] = set()
    account_keys: list[str] = []
    saw_blank = False
    for value in raw_labels:
        normalized = (value or "").strip()
        if not normalized:
            saw_blank = True
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        account_keys.append(normalized)
    account_keys.sort(key=str.lower)
    if saw_blank:
        account_keys.insert(0, "__UNLABELED__")

    risk_profile = None
    try:
        from .models import UserRiskProfile
        risk_profile = UserRiskProfile.objects.filter(user=user).first()
    except Exception:
        risk_profile = None

    max_position_weight_pct = Decimal(getattr(risk_profile, "max_position_weight_pct", 0) or 0).quantize(Decimal("0.01")) if risk_profile else Decimal("0.00")
    max_net_exposure_pct = Decimal(getattr(risk_profile, "max_net_exposure_pct", 0) or 0).quantize(Decimal("0.01")) if risk_profile else Decimal("0.00")
    concentration_warning_buffer_pct = Decimal(getattr(risk_profile, "concentration_warning_buffer_pct", 0) or 0).quantize(Decimal("0.01")) if risk_profile else Decimal("0.00")
    net_exposure_warning_buffer_pct = Decimal(getattr(risk_profile, "net_exposure_warning_buffer_pct", 0) or 0).quantize(Decimal("0.01")) if risk_profile else Decimal("0.00")

    def _posture(value_pct: Decimal | None, limit_pct: Decimal, buffer_pct: Decimal) -> tuple[str | None, Decimal | None]:
        if value_pct is None or limit_pct <= 0:
            return None, None
        headroom = (limit_pct - value_pct).quantize(Decimal("0.01"))
        near_floor = max(Decimal("0.00"), limit_pct - buffer_pct)
        if value_pct > limit_pct:
            return "OVER", headroom
        if value_pct >= near_floor:
            return "NEAR", headroom
        return "OK", headroom

    rows = []
    posture_counts = {"OVER": 0, "NEAR": 0, "OK": 0, "NO_EQUITY": 0}
    for account_key in account_keys:
        display_label = "Unlabeled / blended" if account_key == "__UNLABELED__" else account_key
        exposure = summarize_portfolio_exposure(user=user, account_label=account_key)
        broker_posture = summarize_broker_snapshot_posture(user=user, account_label=account_key)
        latest_run_qs = BrokerPositionImportRun.objects.filter(user=user)
        if account_key == "__UNLABELED__":
            latest_run_qs = latest_run_qs.filter(account_label="")
        else:
            latest_run_qs = latest_run_qs.filter(account_label__iexact=account_key)
        latest_run = latest_run_qs.order_by("-created_at", "-id").first()

        posture_equity = broker_posture.get("snapshot_equity")
        equity_source = "broker snapshot" if posture_equity is not None else None
        if posture_equity is None and len(account_keys) <= 1:
            posture_equity = exposure.get("account_equity")
            if posture_equity is not None:
                equity_source = "allocation profile"

        deployment_pct = None
        position_pct_of_equity = None
        largest_position = exposure.get("top_positions", [None])[0] if exposure.get("top_positions") else None
        if posture_equity is not None and posture_equity > 0:
            deployment_pct = ((Decimal(exposure.get("total_market_value") or 0) / Decimal(posture_equity)) * Decimal("100")).quantize(Decimal("0.01"))
            if largest_position is not None:
                position_pct_of_equity = ((Decimal(largest_position["market_value"]) / Decimal(posture_equity)) * Decimal("100")).quantize(Decimal("0.01"))

        deployment_posture, deployment_headroom_pct = _posture(deployment_pct, max_net_exposure_pct, net_exposure_warning_buffer_pct)
        position_posture, position_headroom_pct = _posture(position_pct_of_equity, max_position_weight_pct, concentration_warning_buffer_pct)
        drift_posture = broker_posture.get("drift_posture")
        unresolved_count = int(getattr(latest_run, "unresolved_count", 0) or 0)
        missing_from_import = HeldPosition.objects.filter(user=user, status=HeldPosition.Status.OPEN, missing_from_latest_import=True).filter(account_label="" if account_key == "__UNLABELED__" else account_key).count() if account_key == "__UNLABELED__" else HeldPosition.objects.filter(user=user, status=HeldPosition.Status.OPEN, missing_from_latest_import=True, account_label__iexact=account_key).count()

        if deployment_posture == "OVER" or position_posture == "OVER" or drift_posture == "LARGE":
            overall_posture = "OVER"
        elif deployment_posture == "NEAR" or position_posture == "NEAR" or drift_posture == "MEDIUM" or unresolved_count > 0 or missing_from_import > 0:
            overall_posture = "NEAR"
        elif posture_equity is None:
            overall_posture = "NO_EQUITY"
        else:
            overall_posture = "OK"
        posture_counts[overall_posture] = posture_counts.get(overall_posture, 0) + 1

        rows.append({
            "account_key": account_key,
            "account_label": display_label,
            "exposure": exposure,
            "broker_posture": broker_posture,
            "posture_equity": posture_equity,
            "equity_source": equity_source,
            "deployment_pct": deployment_pct,
            "deployment_posture": deployment_posture,
            "deployment_headroom_pct": deployment_headroom_pct,
            "largest_position": largest_position,
            "largest_position_pct_of_equity": position_pct_of_equity,
            "largest_position_posture": position_posture,
            "largest_position_headroom_pct": position_headroom_pct,
            "unresolved_count": unresolved_count,
            "latest_run": latest_run,
            "missing_from_latest_import": missing_from_import,
            "overall_posture": overall_posture,
            "holdings_url_account": "" if account_key == "__UNLABELED__" else account_key,
        })

    return {
        "rows": rows,
        "count": len(rows),
        "posture_counts": posture_counts,
    }


def _classify_broker_drift(*, market_value_drift_abs: Decimal, market_value_drift_pct: Decimal | None) -> str:
    if market_value_drift_abs >= Decimal("1000") or (market_value_drift_pct is not None and market_value_drift_pct >= Decimal("10.00")):
        return "LARGE"
    if market_value_drift_abs >= Decimal("250") or (market_value_drift_pct is not None and market_value_drift_pct >= Decimal("3.00")):
        return "MEDIUM"
    return "SMALL"


def summarize_broker_snapshot_posture(*, user, account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    normalized_account_label = "" if account_label == "__UNLABELED__" else account_label
    exposure = summarize_portfolio_exposure(user=user, account_label=account_label)
    snapshots_qs = ImportedBrokerSnapshot.objects.filter(user=user)
    if account_label == "__UNLABELED__":
        snapshots_qs = snapshots_qs.filter(account_label="")
    elif normalized_account_label:
        snapshots_qs = snapshots_qs.filter(account_label__iexact=normalized_account_label)
    latest = snapshots_qs.order_by("-as_of", "-id").first()

    latest_per_account = []
    combined_equity = Decimal("0.00")
    combined_cash = Decimal("0.00")
    combined_invested = Decimal("0.00")
    seen_accounts: set[str] = set()
    for snap in ImportedBrokerSnapshot.objects.filter(user=user).order_by("account_label", "-as_of", "-id"):
        key = (snap.account_label or "").strip().lower() or "__blank__"
        if key in seen_accounts:
            continue
        seen_accounts.add(key)
        equity = Decimal(snap.account_equity or 0).quantize(Decimal("0.01"))
        cash = Decimal(snap.cash_balance or 0).quantize(Decimal("0.01"))
        invested = (equity - cash).quantize(Decimal("0.01"))
        combined_equity += equity
        combined_cash += cash
        combined_invested += invested
        latest_per_account.append({
            "snapshot": snap,
            "account_label": (snap.account_label or "").strip() or "Unlabeled account",
            "snapshot_equity": equity,
            "snapshot_cash": cash,
            "snapshot_invested": invested,
        })

    if latest is None:
        return {
            "latest": None,
            "snapshot_equity": None,
            "snapshot_cash": None,
            "snapshot_invested": None,
            "tracked_market_value": exposure.get("total_market_value"),
            "market_value_drift": None,
            "market_value_drift_pct": None,
            "drift_posture": None,
            "account_equity_drift": None,
            "account_equity_drift_pct": None,
            "profile_equity": exposure.get("account_equity"),
            "selected_account_label": account_label,
            "latest_per_account": latest_per_account,
            "combined_latest_snapshot_count": len(latest_per_account),
            "combined_snapshot_equity": combined_equity.quantize(Decimal("0.01")),
            "combined_snapshot_cash": combined_cash.quantize(Decimal("0.01")),
            "combined_snapshot_invested": combined_invested.quantize(Decimal("0.01")),
            "combined_market_value_drift": None,
            "combined_market_value_drift_pct": None,
            "combined_drift_posture": None,
        }

    snapshot_equity = Decimal(latest.account_equity or 0).quantize(Decimal("0.01"))
    snapshot_cash = Decimal(latest.cash_balance or 0).quantize(Decimal("0.01"))
    snapshot_invested = (snapshot_equity - snapshot_cash).quantize(Decimal("0.01"))
    tracked_market_value = Decimal(exposure.get("total_market_value") or 0).quantize(Decimal("0.01"))
    market_value_drift = (tracked_market_value - snapshot_invested).quantize(Decimal("0.01"))
    market_value_drift_abs = abs(market_value_drift)
    market_value_drift_pct = None
    if snapshot_invested > 0:
        market_value_drift_pct = ((market_value_drift_abs / snapshot_invested) * Decimal("100")).quantize(Decimal("0.01"))

    profile_equity = exposure.get("account_equity")
    account_equity_drift = None
    account_equity_drift_pct = None
    if profile_equity is not None:
        profile_equity = Decimal(profile_equity).quantize(Decimal("0.01"))
        account_equity_drift = (profile_equity - snapshot_equity).quantize(Decimal("0.01"))
        if snapshot_equity > 0:
            account_equity_drift_pct = ((abs(account_equity_drift) / snapshot_equity) * Decimal("100")).quantize(Decimal("0.01"))

    drift_posture = _classify_broker_drift(market_value_drift_abs=market_value_drift_abs, market_value_drift_pct=market_value_drift_pct)

    combined_market_value_drift = None
    combined_market_value_drift_pct = None
    combined_drift_posture = None
    if latest_per_account:
        combined_market_value_drift = (tracked_market_value - combined_invested.quantize(Decimal("0.01"))).quantize(Decimal("0.01"))
        if combined_invested > 0:
            combined_market_value_drift_pct = ((abs(combined_market_value_drift) / combined_invested) * Decimal("100")).quantize(Decimal("0.01"))
        combined_drift_posture = _classify_broker_drift(
            market_value_drift_abs=abs(combined_market_value_drift),
            market_value_drift_pct=combined_market_value_drift_pct,
        )

    return {
        "latest": latest,
        "snapshot_equity": snapshot_equity,
        "snapshot_cash": snapshot_cash,
        "snapshot_invested": snapshot_invested,
        "tracked_market_value": tracked_market_value,
        "market_value_drift": market_value_drift,
        "market_value_drift_pct": market_value_drift_pct,
        "drift_posture": drift_posture,
        "account_equity_drift": account_equity_drift,
        "account_equity_drift_pct": account_equity_drift_pct,
        "profile_equity": profile_equity,
        "selected_account_label": account_label,
        "latest_per_account": latest_per_account,
        "combined_latest_snapshot_count": len(latest_per_account),
        "combined_snapshot_equity": combined_equity.quantize(Decimal("0.01")),
        "combined_snapshot_cash": combined_cash.quantize(Decimal("0.01")),
        "combined_snapshot_invested": combined_invested.quantize(Decimal("0.01")),
        "combined_market_value_drift": combined_market_value_drift,
        "combined_market_value_drift_pct": combined_market_value_drift_pct,
        "combined_drift_posture": combined_drift_posture,
    }


def summarize_holding_sector_exposure(*, user, account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    risk_profile = None
    max_sector_weight_pct = None
    concentration_warning_buffer_pct = None
    try:
        from .models import UserRiskProfile
        risk_profile = UserRiskProfile.objects.filter(user=user).first()
        if risk_profile:
            max_sector_weight_pct = Decimal(risk_profile.max_sector_weight_pct or 0).quantize(Decimal("0.01"))
            concentration_warning_buffer_pct = Decimal(risk_profile.concentration_warning_buffer_pct or 0).quantize(Decimal("0.01"))
    except Exception:
        risk_profile = None

    watchlist = None
    try:
        from .watchlists import ensure_active_watchlist
        watchlist = ensure_active_watchlist(user)
    except Exception:
        watchlist = None

    sector_map: dict[int, str] = {}
    if watchlist is not None:
        for selection in InstrumentSelection.objects.filter(watchlist=watchlist, is_active=True):
            sector_map[selection.instrument_id] = _normalize_watchlist_sector_label(selection.sector)

    qs = (
        HeldPosition.objects.select_related("instrument")
        .filter(user=user, status=HeldPosition.Status.OPEN)
        .order_by("instrument__symbol")
    )
    if account_label == "__UNLABELED__":
        qs = qs.filter(account_label="")
    elif account_label:
        qs = qs.filter(account_label__iexact=account_label)
    positions = list(qs)
    total_market_value = Decimal("0")
    buckets: dict[str, dict] = {}

    for position in positions:
        refreshed = refresh_position_market_state(position)
        price_used = Decimal(refreshed.last_price) if refreshed.last_price is not None else Decimal(refreshed.average_entry_price)
        market_value = (Decimal(refreshed.quantity) * price_used).quantize(Decimal("0.01"))
        cost_basis = (Decimal(refreshed.quantity) * Decimal(refreshed.average_entry_price)).quantize(Decimal("0.01"))
        unrealized_pnl = (market_value - cost_basis).quantize(Decimal("0.01"))
        total_market_value += market_value

        sector = sector_map.get(refreshed.instrument_id, "Unassigned")
        bucket = buckets.setdefault(sector, {
            "sector": sector,
            "count": 0,
            "market_value": Decimal("0.00"),
            "unrealized_pnl": Decimal("0.00"),
            "positions": [],
        })
        bucket["count"] += 1
        bucket["market_value"] += market_value
        bucket["unrealized_pnl"] += unrealized_pnl
        bucket["positions"].append({
            "id": refreshed.id,
            "symbol": refreshed.instrument.symbol,
            "market_value": market_value,
        })

    rows = []
    near_limit_floor = None
    if max_sector_weight_pct is not None and concentration_warning_buffer_pct is not None:
        near_limit_floor = max(Decimal("0.00"), max_sector_weight_pct - concentration_warning_buffer_pct)

    for sector, bucket in buckets.items():
        market_value = bucket["market_value"].quantize(Decimal("0.01"))
        weight_pct = None
        if total_market_value > 0:
            weight_pct = ((market_value / total_market_value) * Decimal("100")).quantize(Decimal("0.01"))
        top_positions = sorted(bucket["positions"], key=lambda item: (-item["market_value"], item["symbol"]))[:3]
        sector_total = market_value
        top_symbols = []
        for item in top_positions:
            sector_weight = None
            if sector_total > 0:
                sector_weight = ((item["market_value"] / sector_total) * Decimal("100")).quantize(Decimal("0.01"))
            top_symbols.append({
                **item,
                "weight_pct": sector_weight,
            })
        cap_posture = "OK"
        cap_headroom_pct = None
        if weight_pct is not None and max_sector_weight_pct is not None:
            cap_headroom_pct = (max_sector_weight_pct - weight_pct).quantize(Decimal("0.01"))
            if weight_pct > max_sector_weight_pct:
                cap_posture = "OVER"
            elif near_limit_floor is not None and weight_pct >= near_limit_floor:
                cap_posture = "NEAR"
        rows.append({
            "sector": sector,
            "count": bucket["count"],
            "market_value": market_value,
            "weight_pct": weight_pct,
            "unrealized_pnl": bucket["unrealized_pnl"].quantize(Decimal("0.01")),
            "top_symbols": top_symbols,
            "cap_posture": cap_posture,
            "cap_headroom_pct": cap_headroom_pct,
        })

    rows.sort(key=lambda item: (-(item["weight_pct"] or Decimal("0")), item["sector"].lower()))
    unassigned_count = buckets.get("Unassigned", {}).get("count", 0)
    top_sector = rows[0] if rows else None
    over_cap_count = sum(1 for row in rows if row["cap_posture"] == "OVER")
    near_cap_count = sum(1 for row in rows if row["cap_posture"] == "NEAR")
    return {
        "sector_count": len(rows),
        "total_market_value": total_market_value.quantize(Decimal("0.01")),
        "unassigned_count": unassigned_count,
        "top_sector": top_sector,
        "rows": rows,
        "max_sector_weight_pct": max_sector_weight_pct,
        "concentration_warning_buffer_pct": concentration_warning_buffer_pct,
        "over_cap_count": over_cap_count,
        "near_cap_count": near_cap_count,
    }

def _holding_alert_message(snapshot: HoldingHealthSnapshot, alert_type: str) -> str:
    position = snapshot.position
    current = snapshot.current_price
    current_text = str(current) if current is not None else "n/a"
    pnl_text = f"{snapshot.pnl_pct:.2f}%" if snapshot.pnl_pct is not None else "n/a"
    if alert_type == HoldingAlert.AlertType.STOP_BREACH:
        return f"{position.instrument.symbol} is below your stop ({position.stop_price}). Last price {current_text}. PnL {pnl_text}. Recommendation: {snapshot.recommendation_label}."
    if alert_type == HoldingAlert.AlertType.TARGET_REACHED:
        return f"{position.instrument.symbol} reached your target ({position.target_price}). Last price {current_text}. PnL {pnl_text}. Recommendation: {snapshot.recommendation_label}."
    if alert_type == HoldingAlert.AlertType.DETERIORATING:
        return f"{position.instrument.symbol} is down {pnl_text} from your entry ({position.average_entry_price}). Recommendation: {snapshot.recommendation_label}."
    if snapshot.opposing_signal is not None:
        return f"{position.instrument.symbol} now has a live SHORT signal from {snapshot.opposing_signal.strategy.slug} on {snapshot.opposing_signal.timeframe}. Last price {current_text}. Recommendation: {snapshot.recommendation_label}."
    return f"Review {position.instrument.symbol}. Status: {snapshot.status_label}. Recommendation: {snapshot.recommendation_label}."



def _holding_alert_payload(snapshot: HoldingHealthSnapshot, alert_type: str, message: str) -> dict:
    position = snapshot.position
    return {
        "content": f"Holding alert — {position.instrument.symbol}",
        "embeds": [
            {
                "title": f"{position.instrument.symbol} holding check",
                "description": message,
                "color": 0xC0392B if alert_type != HoldingAlert.AlertType.TARGET_REACHED else 0x27AE60,
                "fields": [
                    {"name": "Qty", "value": str(position.quantity), "inline": True},
                    {"name": "Entry", "value": str(position.average_entry_price), "inline": True},
                    {"name": "Last", "value": str(snapshot.current_price or "n/a"), "inline": True},
                    {"name": "Recommendation", "value": snapshot.recommendation_label, "inline": True},
                ],
                "footer": {"text": "Trading Advisor — manual execution / held position monitoring"},
            }
        ],
    }



def _recent_holding_alert_exists(position: HeldPosition, alert_type: str, channel: str) -> bool:
    cooldown = max(1, int(getattr(settings, "HELD_POSITION_ALERT_COOLDOWN_MINUTES", 240) or 240))
    cutoff = timezone.now() - timedelta(minutes=cooldown)
    return HoldingAlert.objects.filter(
        position=position,
        alert_type=alert_type,
        channel=channel,
        status=HoldingAlert.Status.SENT,
        created_at__gte=cutoff,
    ).exists()



def evaluate_position_alerts(position: HeldPosition, *, dry_run: bool = False) -> list[HoldingAlert]:
    snapshot = build_holding_health_snapshot(position)
    alert_types: list[str] = []
    if snapshot.stop_breached:
        alert_types.append(HoldingAlert.AlertType.STOP_BREACH)
    if snapshot.thesis_broken:
        alert_types.append(HoldingAlert.AlertType.THESIS_BREAK)
    if snapshot.deteriorating:
        alert_types.append(HoldingAlert.AlertType.DETERIORATING)
    if snapshot.target_reached:
        alert_types.append(HoldingAlert.AlertType.TARGET_REACHED)

    created: list[HoldingAlert] = []
    channels = get_enabled_delivery_channels()
    for alert_type in alert_types:
        message = _holding_alert_message(snapshot, alert_type)
        payload = _holding_alert_payload(snapshot, alert_type, message)
        for channel in channels:
            if _recent_holding_alert_exists(position, alert_type, channel):
                created.append(HoldingAlert.objects.create(position=position, alert_type=alert_type, channel=channel, status=HoldingAlert.Status.SKIPPED, reason="cooldown", message=message, payload_snapshot=payload))
                continue
            if dry_run:
                created.append(HoldingAlert.objects.create(position=position, alert_type=alert_type, channel=channel, status=HoldingAlert.Status.DRY_RUN, reason="dry_run", message=message, payload_snapshot=payload))
                continue
            try:
                if channel == "DISCORD":
                    _post_discord(getattr(settings, "DISCORD_WEBHOOK_URL", "").strip(), payload)
                elif channel == "EMAIL":
                    send_mail(
                        subject=f"Holding alert: {position.instrument.symbol}",
                        message=message,
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "webmaster@localhost"),
                        recipient_list=[getattr(settings, "ALERT_EMAIL_TO", "")],
                        fail_silently=False,
                    )
                created.append(HoldingAlert.objects.create(position=position, alert_type=alert_type, channel=channel, status=HoldingAlert.Status.SENT, reason="sent", message=message, payload_snapshot=payload, delivered_at=timezone.now()))
            except Exception as exc:  # noqa: BLE001
                created.append(HoldingAlert.objects.create(position=position, alert_type=alert_type, channel=channel, status=HoldingAlert.Status.FAILED, reason="exception", message=message, payload_snapshot=payload, error_message=str(exc)))
    return created



def check_open_held_positions(*, user=None, dry_run: bool = False) -> list[HoldingAlert]:
    qs = HeldPosition.objects.select_related("instrument", "user").filter(status=HeldPosition.Status.OPEN)
    if user is not None:
        qs = qs.filter(user=user)
    out: list[HoldingAlert] = []
    for position in qs:
        out.extend(evaluate_position_alerts(position, dry_run=dry_run))
    return out


@dataclass(frozen=True)
class HoldingImportPreviewRow:
    row_number: int
    symbol: str
    quantity: Decimal | None
    average_entry_price: Decimal | None
    opened_at_iso: str
    stop_price: Decimal | None
    target_price: Decimal | None
    thesis: str
    notes: str
    instrument_id: int | None
    instrument_symbol: str
    status: str
    message: str


_IMPORT_HEADER_ALIASES = {
    "symbol": {"symbol", "ticker", "instrument", "stock", "asset"},
    "quantity": {"quantity", "qty", "shares", "units"},
    "average_entry_price": {"average_entry_price", "avg_entry", "average price", "avg price", "entry", "entry_price", "cost_basis", "cost basis"},
    "opened_at": {"opened_at", "open_date", "opened", "purchase_date", "date"},
    "stop_price": {"stop_price", "stop", "stop loss", "stop_loss"},
    "target_price": {"target_price", "target", "take_profit", "take profit"},
    "thesis": {"thesis", "reason", "plan"},
    "notes": {"notes", "note", "comment", "comments"},
}


def _normalize_header(value: str) -> str:
    return " ".join((value or "").strip().lower().replace("_", " ").split())


def _canonical_header_map(headers: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in headers:
        normalized = _normalize_header(raw)
        for canonical, aliases in _IMPORT_HEADER_ALIASES.items():
            if normalized in aliases and canonical not in out:
                out[canonical] = raw
                break
    return out


def _parse_decimal(value: str) -> Decimal | None:
    raw = (value or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True)
class BrokerPositionImportPreviewRow:
    row_number: int
    symbol: str
    quantity: Decimal | None
    market_price: Decimal | None
    market_value: Decimal | None
    average_entry_price: Decimal | None
    instrument_id: int | None
    instrument_symbol: str
    status: str
    message: str


_BROKER_POSITION_HEADER_ALIASES = {
    "symbol": {"symbol", "ticker", "instrument", "stock", "asset"},
    "quantity": {"quantity", "qty", "shares", "units"},
    "market_price": {"market_price", "price", "last price", "current price", "market price"},
    "market_value": {"market_value", "value", "market value", "position_value", "position value", "current_value", "current value"},
    "average_entry_price": {"average_entry_price", "avg_entry", "average price", "avg price", "entry", "entry_price", "cost_basis", "cost basis", "average cost", "avg cost"},
}


def _canonical_broker_position_header_map(headers: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in headers:
        normalized = _normalize_header(raw)
        for canonical, aliases in _BROKER_POSITION_HEADER_ALIASES.items():
            if normalized in aliases and canonical not in out:
                out[canonical] = raw
                break
    return out


def parse_broker_position_import_csv(file_obj) -> dict:
    import csv
    import io

    raw = file_obj.read()
    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig", errors="replace")
    else:
        text = raw
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    header_map = _canonical_broker_position_header_map(headers)
    required = ["symbol", "quantity"]
    missing_required = [name for name in required if name not in header_map]

    rows: list[BrokerPositionImportPreviewRow] = []
    if missing_required:
        return {
            "rows": rows,
            "headers": headers,
            "header_map": header_map,
            "errors": [f"Missing required columns: {', '.join(missing_required)}."],
        }

    buffered_rows = list(reader)
    symbol_values = [((row.get(header_map["symbol"]) or "").strip().upper()) for row in buffered_rows]
    instruments = {item.symbol.upper(): item for item in Instrument.objects.filter(symbol__in=[s for s in symbol_values if s])}

    errors: list[str] = []
    for idx, row in enumerate(buffered_rows, start=2):
        symbol = (row.get(header_map["symbol"]) or "").strip().upper()
        qty = _parse_decimal(row.get(header_map["quantity"]) or "")
        market_price = _parse_decimal(row.get(header_map.get("market_price", ""), "") if header_map.get("market_price") else "")
        market_value = _parse_decimal(row.get(header_map.get("market_value", ""), "") if header_map.get("market_value") else "")
        average_entry_price = _parse_decimal(row.get(header_map.get("average_entry_price", ""), "") if header_map.get("average_entry_price") else "")
        instrument = instruments.get(symbol)
        status = "ready"
        message = "Ready to compare against tracked holdings."
        if not symbol:
            status = "error"
            message = "Missing symbol."
        elif instrument is None:
            status = "error"
            message = "Symbol not found in instrument universe."
        elif qty is None or qty < 0:
            status = "error"
            message = "Quantity must be zero or a positive number."

        rows.append(BrokerPositionImportPreviewRow(
            row_number=idx,
            symbol=symbol,
            quantity=qty,
            market_price=market_price,
            market_value=market_value,
            average_entry_price=average_entry_price,
            instrument_id=instrument.id if instrument else None,
            instrument_symbol=instrument.symbol if instrument else symbol,
            status=status,
            message=message,
        ))
    return {
        "rows": rows,
        "headers": headers,
        "header_map": header_map,
        "errors": errors,
    }


def build_broker_position_reconciliation(*, user, rows: list[BrokerPositionImportPreviewRow], account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    open_qs = HeldPosition.objects.select_related("instrument").filter(user=user, status=HeldPosition.Status.OPEN)
    if account_label == "__UNLABELED__":
        open_qs = open_qs.filter(account_label="")
    elif account_label:
        open_qs = open_qs.filter(account_label__iexact=account_label)
    open_positions = list(
        open_qs
        .order_by("instrument__symbol", "id")
    )
    open_by_instrument = {position.instrument_id: position for position in open_positions}
    imported_rows = [row for row in rows if row.status == "ready" and row.instrument_id]
    imported_ids = {row.instrument_id for row in imported_rows if row.instrument_id}

    exact_matches = []
    quantity_mismatches = []
    broker_only = []
    tracked_only = []
    total_imported_market_value = Decimal("0")

    for row in imported_rows:
        if row.market_value is not None:
            total_imported_market_value += Decimal(row.market_value)
        tracked = open_by_instrument.get(row.instrument_id)
        if tracked is None:
            broker_only.append({
                "row": row,
                "tracked": None,
            })
            continue

        tracked_qty = Decimal(tracked.quantity)
        imported_qty = Decimal(row.quantity or 0)
        quantity_diff = (tracked_qty - imported_qty).quantize(Decimal("0.00000001"))
        tracked_price = Decimal(tracked.last_price or tracked.average_entry_price or 0)
        tracked_market_value = (tracked_qty * tracked_price).quantize(Decimal("0.01"))
        imported_market_value = None
        if row.market_value is not None:
            imported_market_value = Decimal(row.market_value).quantize(Decimal("0.01"))
        elif row.market_price is not None:
            imported_market_value = (imported_qty * Decimal(row.market_price)).quantize(Decimal("0.01"))
        market_value_diff = None
        if imported_market_value is not None:
            market_value_diff = (tracked_market_value - imported_market_value).quantize(Decimal("0.01"))

        item = {
            "row": row,
            "tracked": tracked,
            "tracked_quantity": tracked_qty,
            "imported_quantity": imported_qty,
            "quantity_diff": quantity_diff,
            "tracked_market_value": tracked_market_value,
            "imported_market_value": imported_market_value,
            "market_value_diff": market_value_diff,
        }
        if abs(quantity_diff) <= Decimal("0.00000001"):
            exact_matches.append(item)
        else:
            quantity_mismatches.append(item)

    for position in open_positions:
        if position.instrument_id not in imported_ids:
            tracked_only.append(position)

    exact_matches.sort(key=lambda item: item["row"].symbol)
    quantity_mismatches.sort(key=lambda item: (abs(item["quantity_diff"]), item["row"].symbol), reverse=True)
    broker_only.sort(key=lambda item: item["row"].symbol)
    tracked_only.sort(key=lambda position: position.instrument.symbol)

    total_tracked_market_value = Decimal("0")
    for position in open_positions:
        price = Decimal(position.last_price or position.average_entry_price or 0)
        total_tracked_market_value += Decimal(position.quantity) * price
    total_tracked_market_value = total_tracked_market_value.quantize(Decimal("0.01"))
    total_imported_market_value = total_imported_market_value.quantize(Decimal("0.01"))
    market_value_drift = (total_tracked_market_value - total_imported_market_value).quantize(Decimal("0.01"))

    return {
        "tracked_open_count": len(open_positions),
        "account_label": account_label,
        "import_ready_count": len(imported_rows),
        "exact_match_count": len(exact_matches),
        "quantity_mismatch_count": len(quantity_mismatches),
        "broker_only_count": len(broker_only),
        "tracked_only_count": len(tracked_only),
        "exact_matches": exact_matches[:25],
        "quantity_mismatches": quantity_mismatches[:50],
        "broker_only": broker_only[:50],
        "tracked_only": tracked_only[:50],
        "total_tracked_market_value": total_tracked_market_value,
        "total_imported_market_value": total_imported_market_value,
        "market_value_drift": market_value_drift,
    }


def serialize_broker_position_import_rows(rows: list[BrokerPositionImportPreviewRow]) -> list[dict]:
    payload: list[dict] = []
    for row in rows:
        payload.append({
            "row_number": row.row_number,
            "symbol": row.symbol,
            "quantity": str(row.quantity) if row.quantity is not None else None,
            "market_price": str(row.market_price) if row.market_price is not None else None,
            "market_value": str(row.market_value) if row.market_value is not None else None,
            "average_entry_price": str(row.average_entry_price) if row.average_entry_price is not None else None,
            "instrument_id": row.instrument_id,
            "instrument_symbol": row.instrument_symbol,
            "status": row.status,
            "message": row.message,
        })
    return payload


def deserialize_broker_position_import_rows(data: list[dict]) -> list[BrokerPositionImportPreviewRow]:
    rows: list[BrokerPositionImportPreviewRow] = []
    for item in data or []:
        rows.append(BrokerPositionImportPreviewRow(
            row_number=int(item.get("row_number") or 0),
            symbol=item.get("symbol") or "",
            quantity=Decimal(str(item["quantity"])) if item.get("quantity") not in (None, "") else None,
            market_price=Decimal(str(item["market_price"])) if item.get("market_price") not in (None, "") else None,
            market_value=Decimal(str(item["market_value"])) if item.get("market_value") not in (None, "") else None,
            average_entry_price=Decimal(str(item["average_entry_price"])) if item.get("average_entry_price") not in (None, "") else None,
            instrument_id=int(item["instrument_id"]) if item.get("instrument_id") else None,
            instrument_symbol=item.get("instrument_symbol") or "",
            status=item.get("status") or "error",
            message=item.get("message") or "",
        ))
    return rows


@dataclass(frozen=True)
class WatchlistImportPreviewRow:
    row_number: int
    symbol: str
    instrument_id: int | None
    instrument_symbol: str
    asset_class: str
    status: str
    message: str
    action: str


_WATCHLIST_IMPORT_HEADER_ALIASES = {
    "symbol": {"symbol", "ticker", "instrument", "stock", "asset"},
}


def _canonical_watchlist_header_map(headers: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in headers:
        normalized = _normalize_header(raw)
        for canonical, aliases in _WATCHLIST_IMPORT_HEADER_ALIASES.items():
            if normalized in aliases and canonical not in out:
                out[canonical] = raw
                break
    return out


def _parse_watchlist_symbols_text(text: str) -> list[str]:
    import re

    raw = text or ""
    pieces = re.split(r"[\n,;\t ]+", raw)
    return [piece.strip().upper() for piece in pieces if piece.strip()]



def parse_watchlist_import(*, file_obj=None, symbols_text: str = "") -> dict:
    import csv
    import io

    headers: list[str] = []
    errors: list[str] = []
    symbols: list[tuple[int, str]] = []

    if file_obj is not None:
        raw = file_obj.read()
        if isinstance(raw, bytes):
            text = raw.decode("utf-8-sig", errors="replace")
        else:
            text = raw
        reader = csv.DictReader(io.StringIO(text))
        headers = list(reader.fieldnames or [])
        if headers:
            header_map = _canonical_watchlist_header_map(headers)
            symbol_header = header_map.get("symbol")
            if not symbol_header and len(headers) == 1:
                symbol_header = headers[0]
            if not symbol_header:
                return {
                    "rows": [],
                    "headers": headers,
                    "errors": ["Missing symbol column. Accepted headers: symbol, ticker, instrument."],
                }
            buffered_rows = list(reader)
            for idx, row in enumerate(buffered_rows, start=2):
                symbols.append((idx, (row.get(symbol_header) or "").strip().upper()))
        else:
            plain_symbols = _parse_watchlist_symbols_text(text)
            symbols = [(idx, symbol) for idx, symbol in enumerate(plain_symbols, start=1)]
    else:
        plain_symbols = _parse_watchlist_symbols_text(symbols_text)
        symbols = [(idx, symbol) for idx, symbol in enumerate(plain_symbols, start=1)]

    if not symbols:
        return {"rows": [], "headers": headers, "errors": ["No symbols were found in the uploaded content."]}

    requested_symbols = [symbol for _, symbol in symbols if symbol]
    instruments = {item.symbol.upper(): item for item in Instrument.objects.filter(symbol__in=requested_symbols, is_active=True)}

    rows: list[WatchlistImportPreviewRow] = []
    seen_ready_ids: set[int] = set()
    for row_number, symbol in symbols:
        instrument = instruments.get(symbol)
        status = "ready"
        message = "Ready to add or keep active."
        action = "activate"
        if not symbol:
            status = "error"
            message = "Missing symbol."
            action = "error"
        elif instrument is None:
            status = "error"
            message = "Symbol not found in active instrument universe."
            action = "error"
        elif instrument.id in seen_ready_ids:
            status = "duplicate"
            message = "Duplicate symbol in import; first occurrence will be used."
            action = "skip-duplicate"
        else:
            seen_ready_ids.add(instrument.id)

        rows.append(WatchlistImportPreviewRow(
            row_number=row_number,
            symbol=symbol,
            instrument_id=instrument.id if instrument else None,
            instrument_symbol=instrument.symbol if instrument else symbol,
            asset_class=instrument.asset_class if instrument else "",
            status=status,
            message=message,
            action=action,
        ))
    return {"rows": rows, "headers": headers, "errors": errors}


def serialize_watchlist_import_rows(rows: list[WatchlistImportPreviewRow]) -> list[dict]:
    return [
        {
            "row_number": row.row_number,
            "symbol": row.symbol,
            "instrument_id": row.instrument_id,
            "instrument_symbol": row.instrument_symbol,
            "asset_class": row.asset_class,
            "status": row.status,
            "message": row.message,
            "action": row.action,
        }
        for row in rows
    ]


def deserialize_watchlist_import_rows(data: list[dict]) -> list[WatchlistImportPreviewRow]:
    rows: list[WatchlistImportPreviewRow] = []
    for item in data or []:
        rows.append(WatchlistImportPreviewRow(
            row_number=int(item.get("row_number") or 0),
            symbol=item.get("symbol") or "",
            instrument_id=int(item["instrument_id"]) if item.get("instrument_id") else None,
            instrument_symbol=item.get("instrument_symbol") or "",
            asset_class=item.get("asset_class") or "",
            status=item.get("status") or "error",
            message=item.get("message") or "",
            action=item.get("action") or "error",
        ))
    return rows


def build_watchlist_import_reconciliation(*, watchlist: Watchlist, rows: list[WatchlistImportPreviewRow]) -> dict:
    imported_ids = {row.instrument_id for row in rows if row.status == "ready" and row.instrument_id}
    active_ids = set(
        InstrumentSelection.objects.filter(watchlist=watchlist, is_active=True).values_list("instrument_id", flat=True)
    )
    missing_ids = active_ids - imported_ids
    matched_ids = active_ids & imported_ids
    added_ids = imported_ids - active_ids
    missing_symbols = list(
        Instrument.objects.filter(id__in=missing_ids).order_by("symbol").values_list("symbol", flat=True)[:50]
    )
    return {
        "active_count": len(active_ids),
        "matched_count": len(matched_ids),
        "new_count": len(added_ids),
        "missing_count": len(missing_ids),
        "missing_symbols": missing_symbols,
    }


def apply_watchlist_import_rows(*, watchlist: Watchlist, rows: list[WatchlistImportPreviewRow], replace_missing: bool = False) -> dict:
    active_map = {
        item.instrument_id: item
        for item in InstrumentSelection.objects.filter(watchlist=watchlist)
    }
    ready_ids: set[int] = set()
    created = 0
    reactivated = 0
    kept = 0
    skipped = 0
    for row in rows:
        if row.status != "ready" or not row.instrument_id:
            skipped += 1
            continue
        ready_ids.add(row.instrument_id)
        selection = active_map.get(row.instrument_id)
        if selection is None:
            InstrumentSelection.objects.create(watchlist=watchlist, instrument_id=row.instrument_id, is_active=True)
            created += 1
        elif not selection.is_active:
            selection.is_active = True
            selection.save(update_fields=["is_active"])
            reactivated += 1
        else:
            kept += 1

    deactivated = 0
    if replace_missing:
        deactivated = (
            InstrumentSelection.objects.filter(watchlist=watchlist, is_active=True)
            .exclude(instrument_id__in=ready_ids)
            .update(is_active=False)
        )

    return {
        "created": created,
        "reactivated": reactivated,
        "kept": kept,
        "skipped": skipped,
        "deactivated": deactivated,
    }


def parse_holding_import_csv(file_obj) -> dict:
    import csv
    import io

    raw = file_obj.read()
    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig", errors="replace")
    else:
        text = raw
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    header_map = _canonical_header_map(headers)
    required = ["symbol", "quantity", "average_entry_price"]
    missing_required = [name for name in required if name not in header_map]

    rows: list[HoldingImportPreviewRow] = []
    if missing_required:
        return {
            "rows": rows,
            "headers": headers,
            "header_map": header_map,
            "errors": [f"Missing required columns: {', '.join(missing_required)}."],
        }

    symbol_values = []
    buffered_rows = list(reader)
    for row in buffered_rows:
        symbol_values.append((row.get(header_map["symbol"]) or "").strip().upper())
    instruments = {item.symbol.upper(): item for item in Instrument.objects.filter(symbol__in=[s for s in symbol_values if s])}

    errors: list[str] = []
    for idx, row in enumerate(buffered_rows, start=2):
        symbol = (row.get(header_map["symbol"]) or "").strip().upper()
        qty = _parse_decimal(row.get(header_map["quantity"]) or "")
        entry = _parse_decimal(row.get(header_map["average_entry_price"]) or "")
        stop = _parse_decimal(row.get(header_map.get("stop_price", ""), "") if header_map.get("stop_price") else "")
        target = _parse_decimal(row.get(header_map.get("target_price", ""), "") if header_map.get("target_price") else "")
        opened_text = (row.get(header_map.get("opened_at", "")) or "").strip() if header_map.get("opened_at") else ""
        thesis = (row.get(header_map.get("thesis", "")) or "").strip() if header_map.get("thesis") else ""
        notes = (row.get(header_map.get("notes", "")) or "").strip() if header_map.get("notes") else ""
        instrument = instruments.get(symbol)

        status = "ready"
        message = "Ready to import."
        if not symbol:
            status = "error"
            message = "Missing symbol."
        elif instrument is None:
            status = "error"
            message = "Symbol not found in instrument universe."
        elif qty is None or qty <= 0:
            status = "error"
            message = "Quantity must be a positive number."
        elif entry is None or entry <= 0:
            status = "error"
            message = "Average entry price must be a positive number."

        rows.append(HoldingImportPreviewRow(
            row_number=idx,
            symbol=symbol,
            quantity=qty,
            average_entry_price=entry,
            opened_at_iso=opened_text,
            stop_price=stop,
            target_price=target,
            thesis=thesis,
            notes=notes,
            instrument_id=instrument.id if instrument else None,
            instrument_symbol=instrument.symbol if instrument else symbol,
            status=status,
            message=message,
        ))
    return {
        "rows": rows,
        "headers": headers,
        "header_map": header_map,
        "errors": errors,
    }


def serialize_import_rows(rows: list[HoldingImportPreviewRow]) -> list[dict]:
    payload: list[dict] = []
    for row in rows:
        payload.append({
            "row_number": row.row_number,
            "symbol": row.symbol,
            "quantity": str(row.quantity) if row.quantity is not None else None,
            "average_entry_price": str(row.average_entry_price) if row.average_entry_price is not None else None,
            "opened_at_iso": row.opened_at_iso,
            "stop_price": str(row.stop_price) if row.stop_price is not None else None,
            "target_price": str(row.target_price) if row.target_price is not None else None,
            "thesis": row.thesis,
            "notes": row.notes,
            "instrument_id": row.instrument_id,
            "instrument_symbol": row.instrument_symbol,
            "status": row.status,
            "message": row.message,
        })
    return payload


def deserialize_import_rows(data: list[dict]) -> list[HoldingImportPreviewRow]:
    rows: list[HoldingImportPreviewRow] = []
    for item in data or []:
        rows.append(HoldingImportPreviewRow(
            row_number=int(item.get("row_number") or 0),
            symbol=item.get("symbol") or "",
            quantity=Decimal(str(item["quantity"])) if item.get("quantity") not in (None, "") else None,
            average_entry_price=Decimal(str(item["average_entry_price"])) if item.get("average_entry_price") not in (None, "") else None,
            opened_at_iso=item.get("opened_at_iso") or "",
            stop_price=Decimal(str(item["stop_price"])) if item.get("stop_price") not in (None, "") else None,
            target_price=Decimal(str(item["target_price"])) if item.get("target_price") not in (None, "") else None,
            thesis=item.get("thesis") or "",
            notes=item.get("notes") or "",
            instrument_id=int(item["instrument_id"]) if item.get("instrument_id") else None,
            instrument_symbol=item.get("instrument_symbol") or "",
            status=item.get("status") or "error",
            message=item.get("message") or "",
        ))
    return rows


def build_holding_import_reconciliation(*, user, rows: list[HoldingImportPreviewRow], account_label: str = "") -> dict:
    account_label = (account_label or "").strip()
    imported_ids = {row.instrument_id for row in rows if row.status == "ready" and row.instrument_id}
    open_qs = HeldPosition.objects.select_related("instrument").filter(user=user, status=HeldPosition.Status.OPEN)
    if account_label == "__UNLABELED__":
        open_qs = open_qs.filter(account_label="")
    elif account_label:
        open_qs = open_qs.filter(account_label__iexact=account_label)
    open_positions = list(
        open_qs
        .order_by("instrument__symbol", "id")
    )
    missing_positions = [position for position in open_positions if position.instrument_id not in imported_ids]
    return {
        "open_positions_count": len(open_positions),
        "matched_count": sum(1 for position in open_positions if position.instrument_id in imported_ids),
        "missing_count": len(missing_positions),
        "missing_positions": missing_positions[:25],
        "missing_symbols": [position.instrument.symbol for position in missing_positions],
        "account_label": account_label,
    }


def _parse_opened_at_or_now(value: str):
    from django.utils.dateparse import parse_datetime, parse_date

    raw = (value or "").strip()
    if not raw:
        return timezone.now()
    dt = parse_datetime(raw)
    if dt is not None:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt
    d = parse_date(raw)
    if d is not None:
        dt = timezone.datetime.combine(d, timezone.datetime.min.time())
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.now()


def apply_holding_import_rows(*, user, rows: list[HoldingImportPreviewRow], mark_missing_review: bool = False, account_label: str = "") -> dict:
    created = 0
    updated = 0
    skipped = 0
    flagged_missing = 0
    touched: list[HeldPosition] = []
    applied_at = timezone.now()
    account_label = (account_label or "").strip()
    if account_label == "__UNLABELED__":
        account_label = ""
    imported_instrument_ids: set[int] = set()
    for row in rows:
        if row.status != "ready" or not row.instrument_id or row.quantity is None or row.average_entry_price is None:
            skipped += 1
            continue
        imported_instrument_ids.add(row.instrument_id)
        position_qs = HeldPosition.objects.filter(user=user, instrument_id=row.instrument_id, status=HeldPosition.Status.OPEN)
        if account_label:
            position_qs = position_qs.filter(account_label__iexact=account_label)
        position = position_qs.order_by('-updated_at', '-id').first()
        opened_at = _parse_opened_at_or_now(row.opened_at_iso)
        if position is None:
            position = HeldPosition.objects.create(
                user=user,
                instrument_id=row.instrument_id,
                status=HeldPosition.Status.OPEN,
                source=HeldPosition.Source.IMPORT,
                quantity=row.quantity,
                average_entry_price=row.average_entry_price,
                opened_at=opened_at,
                account_label=account_label,
                stop_price=row.stop_price,
                target_price=row.target_price,
                thesis=row.thesis,
                notes=row.notes,
                last_import_seen_at=applied_at,
                missing_from_latest_import=False,
            )
            record_holding_transaction(position=position, event_type=HoldingTransaction.EventType.OPEN, quantity=row.quantity, price=row.average_entry_price, notes="Imported open position.", created_at=opened_at)
            created += 1
        else:
            prior_stop = position.stop_price
            position.source = HeldPosition.Source.IMPORT
            position.quantity = row.quantity
            position.average_entry_price = row.average_entry_price
            position.opened_at = opened_at
            position.stop_price = row.stop_price
            position.target_price = row.target_price
            if row.thesis:
                position.thesis = row.thesis
            if row.notes:
                position.notes = row.notes
            position.account_label = account_label
            position.last_import_seen_at = applied_at
            position.missing_from_latest_import = False
            position.save(update_fields=[
                "source", "quantity", "average_entry_price", "opened_at", "account_label",
                "stop_price", "target_price", "thesis", "notes",
                "last_import_seen_at", "missing_from_latest_import", "updated_at",
            ])
            record_holding_transaction(position=position, event_type=HoldingTransaction.EventType.IMPORT_SYNC, quantity=row.quantity, price=row.average_entry_price, notes="Import sync updated open position.", created_at=opened_at)
            resolve_pending_stop_policy_events(position=position, changed_at=applied_at, prior_stop=prior_stop, new_stop=position.stop_price)
            updated += 1
        touched.append(position)

    if mark_missing_review:
        missing_qs = HeldPosition.objects.filter(user=user, status=HeldPosition.Status.OPEN)
        if account_label:
            missing_qs = missing_qs.filter(account_label__iexact=account_label)
        flagged_missing = missing_qs.exclude(instrument_id__in=imported_instrument_ids).update(missing_from_latest_import=True)
    HeldPosition.objects.filter(pk__in=[position.pk for position in touched]).update(missing_from_latest_import=False)
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "flagged_missing": flagged_missing,
        "positions": touched,
    }




def _stop_policy_is_improved(*, prior_stop, new_stop) -> bool:
    if new_stop is None:
        return False
    if prior_stop is None:
        return True
    try:
        return Decimal(new_stop) > Decimal(prior_stop)
    except Exception:
        return False


def resolve_pending_stop_policy_events(*, position: HeldPosition, changed_at=None, prior_stop=None, new_stop=None) -> int:
    if not _stop_policy_is_improved(prior_stop=prior_stop, new_stop=new_stop):
        return 0
    resolved_at = changed_at or timezone.now()
    pending_qs = HoldingTransaction.objects.filter(
        position=position,
        event_type__in=[HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD],
        stop_policy_status="PENDING",
    ).order_by("created_at", "id")
    updated = 0
    for tx in pending_qs:
        status = "ON_TIME"
        if tx.stop_policy_due_at and resolved_at > tx.stop_policy_due_at:
            status = "LATE"
        tx.stop_policy_resolved_at = resolved_at
        tx.stop_policy_status = status
        tx.stop_price_snapshot = new_stop
        tx.save(update_fields=["stop_policy_resolved_at", "stop_policy_status", "stop_price_snapshot"])
        updated += 1
    return updated


def record_holding_transaction(*, position: HeldPosition, event_type: str, quantity: Decimal, price: Decimal, notes: str = "", created_at=None) -> HoldingTransaction:
    entry = Decimal(position.average_entry_price)
    realized = None
    if event_type in {HoldingTransaction.EventType.PARTIAL_SELL, HoldingTransaction.EventType.CLOSE}:
        realized = ((Decimal(price) - entry) * Decimal(quantity)).quantize(Decimal("0.01"))
    guardrail_snapshot = build_holding_health_snapshot(position) if position.status == HeldPosition.Status.OPEN else None
    event_created_at = created_at or timezone.now()
    payload = {
        "position": position,
        "event_type": event_type,
        "quantity": Decimal(quantity),
        "price": Decimal(price),
        "account_label_snapshot": (position.account_label or "").strip(),
        "stop_price_snapshot": position.stop_price,
        "risk_guardrail_posture_snapshot": (guardrail_snapshot.risk_guardrail_posture if guardrail_snapshot else "") or "",
        "risk_guardrail_reason_snapshot": (guardrail_snapshot.risk_guardrail_reason[:120] if guardrail_snapshot and guardrail_snapshot.risk_guardrail_reason else ""),
        "notes": (notes or "").strip(),
        "realized_pnl_amount": realized,
    }
    if event_type in {HoldingTransaction.EventType.OPEN, HoldingTransaction.EventType.BUY_ADD}:
        profile, _ = UserRiskProfile.objects.get_or_create(user=position.user)
        target_hours = max(1, int(getattr(profile, "stop_policy_target_hours", 24) or 24))
        payload["stop_policy_due_at"] = event_created_at + timedelta(hours=target_hours)
        if position.stop_price is not None:
            payload["stop_policy_resolved_at"] = event_created_at
            payload["stop_policy_status"] = "ON_TIME"
        else:
            payload["stop_policy_status"] = "PENDING"
    if created_at is not None:
        payload["created_at"] = created_at
    return HoldingTransaction.objects.create(**payload)


def apply_buy_add(*, position: HeldPosition, buy_quantity: Decimal, buy_price: Decimal | None = None, stop_price: Decimal | None = None, notes: str = "") -> HoldingTransaction:
    refresh_position_market_state(position)
    existing_qty = Decimal(position.quantity)
    if position.status != HeldPosition.Status.OPEN:
        raise ValueError("Only open positions can receive added shares.")
    if buy_quantity <= 0:
        raise ValueError("Buy quantity must be greater than zero.")
    if buy_price is None:
        buy_price = Decimal(position.last_price) if position.last_price is not None else Decimal(position.average_entry_price)
    buy_price = Decimal(buy_price)
    if buy_price <= 0:
        raise ValueError("Buy price must be greater than zero.")

    prior_cost = existing_qty * Decimal(position.average_entry_price)
    prior_stop = position.stop_price
    added_cost = Decimal(buy_quantity) * buy_price
    new_quantity = (existing_qty + Decimal(buy_quantity)).quantize(Decimal("0.00000001"))
    if new_quantity <= 0:
        raise ValueError("New position quantity must be greater than zero.")
    new_average = ((prior_cost + added_cost) / new_quantity).quantize(Decimal("0.00000001"))

    if stop_price is not None:
        stop_price = Decimal(stop_price)
        if stop_price <= 0:
            raise ValueError("Stop price must be greater than zero.")
        position.stop_price = stop_price.quantize(Decimal("0.00000001"))

    tx = record_holding_transaction(
        position=position,
        event_type=HoldingTransaction.EventType.BUY_ADD,
        quantity=Decimal(buy_quantity),
        price=buy_price,
        notes=notes,
    )
    position.quantity = new_quantity
    position.average_entry_price = new_average
    position.status = HeldPosition.Status.OPEN
    position.closed_at = None
    position.close_price = None
    position.close_notes = ""
    position.save(update_fields=["quantity", "average_entry_price", "stop_price", "status", "closed_at", "close_price", "close_notes", "updated_at"])
    if stop_price is not None:
        resolve_pending_stop_policy_events(position=position, changed_at=timezone.now(), prior_stop=prior_stop, new_stop=position.stop_price)
    refresh_position_market_state(position)
    return tx



def apply_account_transfer(*, position: HeldPosition, new_account_label: str = "", notes: str = "") -> HoldingTransaction:
    old_label = (position.account_label or "").strip()
    new_label = (new_account_label or "").strip()
    if old_label == new_label:
        raise ValueError("Choose a different account label to move or relabel this holding.")

    from_label = old_label or "Unlabeled / blended"
    to_label = new_label or "Unlabeled / blended"
    combined_note = f"Account move: {from_label} -> {to_label}."
    if (notes or "").strip():
        combined_note = f"{combined_note} {(notes or '').strip()}"

    reference_price = Decimal(position.last_price) if position.last_price is not None else Decimal(position.average_entry_price)
    tx = record_holding_transaction(
        position=position,
        event_type=HoldingTransaction.EventType.ACCOUNT_TRANSFER,
        quantity=Decimal(position.quantity),
        price=reference_price,
        notes=combined_note,
    )
    position.account_label = new_label
    position.save(update_fields=["account_label", "updated_at"])
    return tx


def apply_partial_sale(*, position: HeldPosition, sell_quantity: Decimal, sale_price: Decimal | None = None, notes: str = "") -> HoldingTransaction:
    refresh_position_market_state(position)
    remaining_qty = Decimal(position.quantity)
    if sell_quantity <= 0:
        raise ValueError("Sell quantity must be greater than zero.")
    if sell_quantity > remaining_qty:
        raise ValueError("Sell quantity cannot exceed the open quantity.")
    if sale_price is None:
        sale_price = Decimal(position.last_price) if position.last_price is not None else Decimal(position.average_entry_price)
    event_type = HoldingTransaction.EventType.CLOSE if sell_quantity == remaining_qty else HoldingTransaction.EventType.PARTIAL_SELL
    tx = record_holding_transaction(position=position, event_type=event_type, quantity=sell_quantity, price=Decimal(sale_price), notes=notes)

    new_qty = (remaining_qty - sell_quantity).quantize(Decimal("0.00000001"))
    update_fields = ["quantity", "updated_at"]
    if new_qty <= 0:
        position.quantity = Decimal("0")
        position.status = HeldPosition.Status.CLOSED
        position.closed_at = tx.created_at
        position.close_price = Decimal(sale_price)
        position.close_notes = (notes or "").strip()
        update_fields.extend(["status", "closed_at", "close_price", "close_notes"])
    else:
        position.quantity = new_qty
    position.save(update_fields=update_fields)
    refresh_position_market_state(position)
    return tx


@dataclass(frozen=True)
class RealizedPositionPerformanceItem:
    position: HeldPosition
    realized_pnl: Decimal
    realized_pct: Decimal | None
    realized_quantity: Decimal


@dataclass(frozen=True)
class UnrealizedPositionPerformanceItem:
    position: HeldPosition
    unrealized_pnl: Decimal
    unrealized_pct: Decimal | None
    market_value: Decimal


def summarize_holding_performance(*, user=None) -> dict:
    qs = HeldPosition.objects.select_related("instrument")
    if user is not None:
        qs = qs.filter(user=user)
    positions = list(qs.order_by("instrument__symbol"))

    open_positions = [refresh_position_market_state(item) for item in positions if item.status == HeldPosition.Status.OPEN]
    closed_positions = [item for item in positions if item.status == HeldPosition.Status.CLOSED]

    unrealized_items: list[UnrealizedPositionPerformanceItem] = []
    total_unrealized = Decimal("0.00")
    total_open_cost_basis = Decimal("0.00")
    for position in open_positions:
        quantity = Decimal(position.quantity)
        entry = Decimal(position.average_entry_price)
        cost_basis = (quantity * entry).quantize(Decimal("0.01"))
        current_price = Decimal(position.last_price) if position.last_price is not None else entry
        market_value = (quantity * current_price).quantize(Decimal("0.01"))
        unrealized = (market_value - cost_basis).quantize(Decimal("0.01"))
        unrealized_pct = None
        if cost_basis > 0:
            unrealized_pct = ((unrealized / cost_basis) * Decimal("100")).quantize(Decimal("0.01"))
        total_unrealized += unrealized
        total_open_cost_basis += cost_basis
        unrealized_items.append(
            UnrealizedPositionPerformanceItem(
                position=position,
                unrealized_pnl=unrealized,
                unrealized_pct=unrealized_pct,
                market_value=market_value,
            )
        )

    realized_items: list[RealizedPositionPerformanceItem] = []
    total_realized = Decimal("0.00")
    winning_realized_positions = 0
    losing_realized_positions = 0
    flat_realized_positions = 0

    for position in positions:
        txs = list(
            position.transactions.filter(
                event_type__in=[HoldingTransaction.EventType.PARTIAL_SELL, HoldingTransaction.EventType.CLOSE]
            ).order_by("created_at", "id")
        )
        if not txs:
            continue
        realized_pnl = sum((tx.realized_pnl_amount or Decimal("0.00")) for tx in txs)
        realized_pnl = Decimal(realized_pnl).quantize(Decimal("0.01"))
        realized_quantity = sum((Decimal(tx.quantity) for tx in txs), Decimal("0"))
        realized_pct = None
        realized_cost_basis = (Decimal(position.average_entry_price) * realized_quantity).quantize(Decimal("0.01"))
        if realized_cost_basis > 0:
            realized_pct = ((realized_pnl / realized_cost_basis) * Decimal("100")).quantize(Decimal("0.01"))
        total_realized += realized_pnl
        if realized_pnl > 0:
            winning_realized_positions += 1
        elif realized_pnl < 0:
            losing_realized_positions += 1
        else:
            flat_realized_positions += 1
        realized_items.append(
            RealizedPositionPerformanceItem(
                position=position,
                realized_pnl=realized_pnl,
                realized_pct=realized_pct,
                realized_quantity=realized_quantity,
            )
        )

    unrealized_items.sort(key=lambda item: item.unrealized_pnl, reverse=True)
    realized_items.sort(key=lambda item: item.realized_pnl, reverse=True)

    realized_positions_count = len(realized_items)
    realized_win_rate = None
    if realized_positions_count:
        realized_win_rate = Decimal(winning_realized_positions * 100 / realized_positions_count).quantize(Decimal("0.01"))
    unrealized_return_pct = None
    if total_open_cost_basis > 0:
        unrealized_return_pct = ((total_unrealized / total_open_cost_basis) * Decimal("100")).quantize(Decimal("0.01"))

    return {
        "open_positions_count": len(open_positions),
        "closed_positions_count": len(closed_positions),
        "realized_positions_count": realized_positions_count,
        "winning_realized_positions": winning_realized_positions,
        "losing_realized_positions": losing_realized_positions,
        "flat_realized_positions": flat_realized_positions,
        "total_realized_pnl": total_realized.quantize(Decimal("0.01")),
        "total_unrealized_pnl": total_unrealized.quantize(Decimal("0.01")),
        "unrealized_return_pct": unrealized_return_pct,
        "realized_win_rate": realized_win_rate,
        "top_realized_winners": realized_items[:5],
        "top_realized_losers": sorted(realized_items, key=lambda item: item.realized_pnl)[:5],
        "top_unrealized_winners": unrealized_items[:5],
        "top_unrealized_losers": sorted(unrealized_items, key=lambda item: item.unrealized_pnl)[:5],
        "recent_closed_positions": sorted(closed_positions, key=lambda item: item.closed_at or item.updated_at, reverse=True)[:8],
    }


def _json_decimal(value):
    if value is None:
        return None
    return str(value)


def build_broker_reconciliation_run_summary(*, reconciliation: dict) -> dict:
    return {
        "tracked_open_count": reconciliation.get("tracked_open_count", 0),
        "import_ready_count": reconciliation.get("import_ready_count", 0),
        "exact_match_count": reconciliation.get("exact_match_count", 0),
        "quantity_mismatch_count": reconciliation.get("quantity_mismatch_count", 0),
        "broker_only_count": reconciliation.get("broker_only_count", 0),
        "tracked_only_count": reconciliation.get("tracked_only_count", 0),
        "total_tracked_market_value": _json_decimal(reconciliation.get("total_tracked_market_value")),
        "total_imported_market_value": _json_decimal(reconciliation.get("total_imported_market_value")),
        "exact_matches": [
            {
                "symbol": item["row"].symbol,
                "tracked_position_id": item["tracked"].pk,
                "tracked_quantity": _json_decimal(item["tracked_quantity"]),
                "imported_quantity": _json_decimal(item["imported_quantity"]),
            }
            for item in reconciliation.get("exact_matches", [])[:25]
        ],
        "quantity_mismatches": [
            {
                "symbol": item["row"].symbol,
                "tracked_position_id": item["tracked"].pk,
                "tracked_quantity": _json_decimal(item["tracked_quantity"]),
                "imported_quantity": _json_decimal(item["imported_quantity"]),
                "quantity_diff": _json_decimal(item["quantity_diff"]),
                "tracked_market_value": _json_decimal(item["tracked_market_value"]),
                "imported_market_value": _json_decimal(item.get("imported_market_value")),
            }
            for item in reconciliation.get("quantity_mismatches", [])
        ],
        "broker_only": [
            {
                "symbol": item["row"].symbol,
                "broker_quantity": _json_decimal(item["row"].quantity),
                "broker_market_price": _json_decimal(item["row"].market_price),
                "broker_market_value": _json_decimal(item["row"].market_value),
            }
            for item in reconciliation.get("broker_only", [])
        ],
        "tracked_only": [
            {
                "symbol": item.instrument.symbol,
                "tracked_position_id": item.pk,
                "tracked_quantity": _json_decimal(item.quantity),
                "missing_from_latest_import": bool(item.missing_from_latest_import),
            }
            for item in reconciliation.get("tracked_only", [])
        ],
    }


def get_broker_reconciliation_issue_symbols(summary: dict) -> list[str]:
    symbols = []
    for key in ("quantity_mismatches", "broker_only", "tracked_only"):
        for item in summary.get(key, []):
            symbol = (item.get("symbol") or "").strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    return symbols


def create_broker_position_import_run(*, user, source_label: str, uploaded_filename: str, preview_rows: list, reconciliation: dict, account_label: str = ""):
    summary = build_broker_reconciliation_run_summary(reconciliation=reconciliation)
    unresolved_count = len(get_broker_reconciliation_issue_symbols(summary))
    return BrokerPositionImportRun.objects.create(
        user=user,
        source_label=(source_label or "Broker CSV").strip() or "Broker CSV",
        account_label=(account_label or "").strip(),
        uploaded_filename=(uploaded_filename or "").strip(),
        summary=summary,
        preview_rows=serialize_broker_position_import_rows(preview_rows),
        unresolved_count=unresolved_count,
    )


def summarize_broker_reconciliation_run(run: BrokerPositionImportRun) -> dict:
    summary = dict(run.summary or {})
    resolutions = {
        item.symbol.upper(): item
        for item in run.resolutions.select_related("tracked_position", "tracked_position__instrument").all()
    }
    issue_symbols = set(get_broker_reconciliation_issue_symbols(summary))
    resolved_symbols = {symbol for symbol in resolutions if symbol in issue_symbols}
    summary["resolved_count"] = len(resolved_symbols)
    summary["unresolved_count"] = max(0, len(issue_symbols) - len(resolved_symbols))
    summary["resolutions"] = resolutions
    return summary


def record_broker_reconciliation_resolution(*, run: BrokerPositionImportRun, user, symbol: str, action: str, note: str = "", tracked_position=None):
    symbol = (symbol or "").strip().upper()
    resolution, _ = BrokerPositionImportResolution.objects.update_or_create(
        run=run,
        symbol=symbol,
        defaults={
            "user": user,
            "tracked_position": tracked_position,
            "action": action,
            "note": note,
            "resolved_at": timezone.now(),
        },
    )
    run.unresolved_count = max(0, len(get_broker_reconciliation_issue_symbols(run.summary or {})) - run.resolutions.count())
    run.save(update_fields=["unresolved_count"])
    return resolution



def run_evidence_lifecycle_automation(*, user, archive_expired: bool = False, soon_days: int = 30) -> dict:
    now = timezone.now()
    soon_cutoff = now + timedelta(days=max(1, int(soon_days or 30)))
    tx_qs = HoldingTransaction.objects.filter(position__user=user).exclude(execution_evidence_attachment="")
    scanned_count = tx_qs.count()
    expiring_qs = tx_qs.filter(execution_evidence_retention_until__isnull=False, execution_evidence_retention_until__gte=now, execution_evidence_retention_until__lt=soon_cutoff)
    expired_qs = tx_qs.filter(execution_evidence_retention_until__lt=now)
    missing_qs = tx_qs.filter(execution_evidence_retention_until__isnull=True)
    archived_count = 0
    if archive_expired:
        for tx in expired_qs.select_related('position__instrument'):
            if getattr(tx, 'execution_evidence_attachment', None):
                tx.execution_evidence_attachment.delete(save=False)
                tx.execution_evidence_attachment = None
                suffix = f"Lifecycle automation archived attachment on {timezone.localtime(now).strftime('%Y-%m-%d %H:%M')}"
                tx.execution_evidence_note = (((tx.execution_evidence_note or '').strip() + '\n' + suffix).strip() if (tx.execution_evidence_note or '').strip() else suffix)
                tx.execution_evidence_recorded_at = now
                tx.save(update_fields=['execution_evidence_attachment', 'execution_evidence_note', 'execution_evidence_recorded_at'])
                archived_count += 1
    notes = []
    if expiring_qs.count():
        notes.append(f"{expiring_qs.count()} expiring soon")
    if expired_qs.count():
        notes.append(f"{expired_qs.count()} expired")
    if missing_qs.count():
        notes.append(f"{missing_qs.count()} missing retention")
    if archived_count:
        notes.append(f"{archived_count} archived")
    run = EvidenceLifecycleAutomationRun.objects.create(
        user=user,
        archive_expired=archive_expired,
        scanned_count=scanned_count,
        attachment_count=scanned_count,
        expiring_soon_count=expiring_qs.count(),
        expired_count=expired_qs.count(),
        missing_retention_count=missing_qs.count(),
        archived_count=archived_count,
        notes=' · '.join(notes),
        created_at=now,
    )
    return {
        'run': run,
        'scanned_count': scanned_count,
        'attachment_count': scanned_count,
        'expiring_soon_count': expiring_qs.count(),
        'expired_count': expired_qs.count(),
        'missing_retention_count': missing_qs.count(),
        'archived_count': archived_count,
        'soon_days': max(1, int(soon_days or 30)),
        'archive_expired': archive_expired,
    }


def summarize_evidence_lifecycle_automation(*, user, soon_days: int = 30) -> dict:
    now = timezone.now()
    soon_cutoff = now + timedelta(days=max(1, int(soon_days or 30)))
    tx_qs = HoldingTransaction.objects.filter(position__user=user).exclude(execution_evidence_attachment='')
    attachment_count = tx_qs.count()
    expiring_soon_count = tx_qs.filter(execution_evidence_retention_until__isnull=False, execution_evidence_retention_until__gte=now, execution_evidence_retention_until__lt=soon_cutoff).count()
    expired_count = tx_qs.filter(execution_evidence_retention_until__lt=now).count()
    missing_retention_count = tx_qs.filter(execution_evidence_retention_until__isnull=True).count()
    recent_runs = list(EvidenceLifecycleAutomationRun.objects.filter(user=user).order_by('-created_at', '-id')[:5])
    last_run = recent_runs[0] if recent_runs else None
    stale_run = bool(not last_run or last_run.created_at < now - timedelta(days=1))
    queue_pressure = 'OVER' if (expired_count or missing_retention_count) else ('NEAR' if expiring_soon_count else 'OK')
    queue_label = 'overdue automation queue' if queue_pressure == 'OVER' else ('watch automation queue' if queue_pressure == 'NEAR' else 'healthy automation queue')
    return {
        'attachment_count': attachment_count,
        'expiring_soon_count': expiring_soon_count,
        'expired_count': expired_count,
        'missing_retention_count': missing_retention_count,
        'soon_days': max(1, int(soon_days or 30)),
        'recent_runs': recent_runs,
        'last_run': last_run,
        'stale_run': stale_run,
        'queue_pressure': queue_pressure,
        'queue_label': queue_label,
        'recommended_action': 'Run archive mode after reviewing expired evidence.' if expired_count else ('Run scan mode to refresh lifecycle posture.' if stale_run else 'No immediate lifecycle action needed.'),
    }


def summarize_portfolio_health_score(*, user) -> dict:
    risk_posture = summarize_account_risk_posture(user=user)
    drawdown_monitoring = summarize_account_drawdown_monitoring(user=user)
    stop_guardrails = summarize_account_stop_guardrails(user=user)
    holding_queues = summarize_account_holding_queues(user=user)
    evidence_lifecycle = summarize_evidence_lifecycle_automation(user=user)

    risk_map = {row["account_key"]: row for row in risk_posture.get("rows", [])}
    drawdown_map = {row["account_key"]: row for row in drawdown_monitoring.get("rows", [])}
    stop_map = {}
    for row in stop_guardrails.get("rows", []):
        key = "__UNLABELED__" if row.get("account_label") == "Unlabeled / blended" else row.get("account_label")
        stop_map[key] = row
    queue_map = {}
    for row in holding_queues.get("rows", []):
        key = "__UNLABELED__" if row.get("account_label") == "Unlabeled / blended" else row.get("account_label")
        queue_map[key] = row

    account_keys = set(risk_map) | set(drawdown_map) | set(stop_map) | set(queue_map)
    rows = []
    weighted_score_seed: list[tuple[Decimal, Decimal]] = []

    def _apply_penalty(score: int, posture: str | None, *, over: int, near: int, alternate: dict | None = None) -> int:
        if alternate and posture in alternate:
            return score - int(alternate[posture])
        if posture == "OVER" or posture == "STRESSED":
            return score - over
        if posture == "NEAR" or posture == "WARNING":
            return score - near
        return score

    def _grade(score: int) -> tuple[str, str]:
        if score >= 85:
            return "STRONG", "Stable"
        if score >= 70:
            return "GOOD", "Working"
        if score >= 50:
            return "WATCH", "Watch"
        if score >= 35:
            return "ACTION", "Action needed"
        return "CRITICAL", "Critical"

    for account_key in sorted(account_keys, key=lambda value: (value != "__UNLABELED__", (value or '').lower())):
        risk_row = risk_map.get(account_key, {})
        drawdown_row = drawdown_map.get(account_key, {})
        stop_row = stop_map.get(account_key, {})
        queue_row = queue_map.get(account_key, {})

        score = 100
        score = _apply_penalty(score, risk_row.get("overall_posture"), over=24, near=10, alternate={"NO_EQUITY": 6})
        score = _apply_penalty(score, drawdown_row.get("overall_posture"), over=24, near=10)
        score = _apply_penalty(score, stop_row.get("overall_posture"), over=22, near=9)
        score = _apply_penalty(score, queue_row.get("overall_posture"), over=18, near=8)

        unresolved_count = int(risk_row.get("unresolved_count") or 0)
        missing_from_import = int(risk_row.get("missing_from_latest_import") or 0)
        if unresolved_count:
            score -= min(8, unresolved_count * 2)
        if missing_from_import:
            score -= min(8, missing_from_import * 2)

        score = max(0, min(100, score))
        grade_code, grade_label = _grade(score)

        actions = []
        if queue_row.get("queue_counts", {}).get("sell_now"):
            actions.append(f"{queue_row['queue_counts']['sell_now']} holding(s) are already in sell-now posture.")
        if stop_row.get("missing_stop_count"):
            actions.append(f"{stop_row['missing_stop_count']} open holding(s) are missing a stop.")
        if stop_row.get("stop_too_wide_count"):
            actions.append(f"{stop_row['stop_too_wide_count']} stop(s) are wider than the configured guardrail.")
        if drawdown_row.get("deep_count"):
            actions.append(f"{drawdown_row['deep_count']} holding(s) are beyond the urgent drawdown line.")
        if unresolved_count:
            actions.append(f"{unresolved_count} broker reconciliation item(s) are still unresolved.")
        if missing_from_import:
            actions.append(f"{missing_from_import} holding(s) are missing from the latest broker import.")
        if not actions:
            actions.append("No immediate account-level health issue is forcing action right now.")

        account_label = risk_row.get("account_label") or drawdown_row.get("account_label") or stop_row.get("account_label") or queue_row.get("account_label") or ("Unlabeled / blended" if account_key == "__UNLABELED__" else account_key)
        tracked_value = Decimal(risk_row.get("exposure", {}).get("total_market_value") or 0)
        weight_value = tracked_value if tracked_value > 0 else Decimal("1")
        weighted_score_seed.append((Decimal(score), weight_value))
        rows.append({
            "account_key": account_key,
            "account_label": account_label,
            "score": score,
            "grade_code": grade_code,
            "grade_label": grade_label,
            "tracked_market_value": tracked_value.quantize(Decimal("0.01")),
            "risk_posture": risk_row.get("overall_posture"),
            "drawdown_posture": drawdown_row.get("overall_posture"),
            "stop_posture": stop_row.get("overall_posture"),
            "queue_posture": queue_row.get("overall_posture"),
            "sell_now_count": int(queue_row.get("queue_counts", {}).get("sell_now") or 0),
            "review_now_count": int(queue_row.get("queue_counts", {}).get("review_now") or 0),
            "missing_stop_count": int(stop_row.get("missing_stop_count") or 0),
            "stop_too_wide_count": int(stop_row.get("stop_too_wide_count") or 0),
            "deep_drawdown_count": int(drawdown_row.get("deep_count") or 0),
            "warning_drawdown_count": int(drawdown_row.get("warning_count") or 0),
            "unresolved_count": unresolved_count,
            "missing_from_latest_import": missing_from_import,
            "largest_position": risk_row.get("largest_position"),
            "worst_snapshot": drawdown_row.get("worst_snapshot"),
            "actions": actions[:3],
            "holdings_url_account": risk_row.get("holdings_url_account", "" if account_key == "__UNLABELED__" else account_key),
        })

    rows.sort(key=lambda row: (row["score"], row["account_label"].lower()))
    total_weight = sum(weight for _, weight in weighted_score_seed)
    if total_weight > 0:
        overall_score = int(round(sum(score * weight for score, weight in weighted_score_seed) / total_weight))
    elif rows:
        overall_score = int(round(sum(row["score"] for row in rows) / len(rows)))
    else:
        overall_score = 100
    overall_grade_code, overall_grade_label = _grade(overall_score)
    weakest = rows[0] if rows else None
    attention_count = sum(1 for row in rows if row["grade_code"] in {"WATCH", "ACTION", "CRITICAL"})
    urgent_count = sum(1 for row in rows if row["grade_code"] in {"ACTION", "CRITICAL"})

    overall_actions = []
    if evidence_lifecycle.get("expired_count") or evidence_lifecycle.get("missing_retention_count"):
        overall_actions.append("Evidence lifecycle queue still needs cleanup or retention completion.")
    if weakest and weakest["grade_code"] in {"ACTION", "CRITICAL"}:
        overall_actions.append(f"{weakest['account_label']} is the weakest account health score and should be reviewed first.")
    if not overall_actions:
        overall_actions.append("No portfolio-wide health issue is dominating the stack right now.")

    return {
        "overall_score": overall_score,
        "overall_grade_code": overall_grade_code,
        "overall_grade_label": overall_grade_label,
        "attention_count": attention_count,
        "urgent_count": urgent_count,
        "weakest_account": weakest,
        "rows": rows,
        "count": len(rows),
        "overall_actions": overall_actions,
    }


def save_portfolio_health_snapshot(*, user) -> PortfolioHealthSnapshot:
    summary = summarize_portfolio_health_score(user=user)
    weakest = summary.get("weakest_account") or {}
    snapshot = PortfolioHealthSnapshot.objects.create(
        user=user,
        overall_score=int(summary.get("overall_score") or 0),
        overall_grade_code=(summary.get("overall_grade_code") or "").strip(),
        overall_grade_label=(summary.get("overall_grade_label") or "").strip(),
        attention_count=int(summary.get("attention_count") or 0),
        urgent_count=int(summary.get("urgent_count") or 0),
        weakest_account_label=(weakest.get("account_label") or "").strip(),
        weakest_account_score=weakest.get("score"),
        summary={
            "overall_actions": summary.get("overall_actions") or [],
            "rows": [
                {
                    "account_label": row.get("account_label"),
                    "score": row.get("score"),
                    "grade_code": row.get("grade_code"),
                    "sell_now_count": row.get("sell_now_count"),
                    "missing_stop_count": row.get("missing_stop_count"),
                    "unresolved_count": row.get("unresolved_count"),
                }
                for row in (summary.get("rows") or [])[:12]
            ],
        },
    )
    return snapshot


def summarize_portfolio_health_history(*, user, limit: int = 12) -> dict:
    snapshots = list(PortfolioHealthSnapshot.objects.filter(user=user).order_by("-created_at", "-id")[:max(1, int(limit or 12))])
    latest = snapshots[0] if snapshots else None
    previous = snapshots[1] if len(snapshots) > 1 else None
    score_delta = None
    urgent_delta = None
    attention_delta = None
    if latest and previous:
        score_delta = latest.overall_score - previous.overall_score
        urgent_delta = latest.urgent_count - previous.urgent_count
        attention_delta = latest.attention_count - previous.attention_count
    trend = "UNCHANGED"
    if score_delta is not None:
        if score_delta >= 5:
            trend = "IMPROVING"
        elif score_delta <= -5:
            trend = "WORSENING"
    return {
        "snapshots": snapshots,
        "latest": latest,
        "previous": previous,
        "score_delta": score_delta,
        "urgent_delta": urgent_delta,
        "attention_delta": attention_delta,
        "trend": trend,
        "has_history": bool(snapshots),
    }
