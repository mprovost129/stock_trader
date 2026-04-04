from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.marketdata.models import Instrument, PriceBar
from apps.signals.models import AlertDelivery, Signal


@dataclass(frozen=True)
class AlertDecision:
    should_send: bool
    reason: str


@dataclass(frozen=True)
class AlertOutcome:
    delivery: AlertDelivery
    action: str


@dataclass(frozen=True)
class AlertExplanation:
    eligible: bool
    reason: str
    score_value: float | None
    score_threshold: float | None
    score_gap: float | None
    age_minutes: int | None
    freshness_limit_minutes: int | None
    session_ok: bool
    session_window: str
    unchanged_state: bool
    state_change_only: bool
    duplicate_sent: bool
    cooldown_hit: bool
    daily_cap_hit: bool
    price_filtered: bool


class DiscordWebhookError(RuntimeError):
    pass


class EmailAlertError(RuntimeError):
    pass


CHANNEL_DISCORD = AlertDelivery.Channel.DISCORD
CHANNEL_EMAIL = AlertDelivery.Channel.EMAIL


def send_test_discord_message(*, title: str = "Trading Advisor test alert", body: str = "Discord webhook wiring is working.", dry_run: bool = False) -> dict:
    webhook_url = getattr(settings, "DISCORD_WEBHOOK_URL", "").strip()
    payload = {
        "content": "🧪 Trading Advisor webhook test",
        "embeds": [
            {
                "title": title,
                "description": body,
                "color": 0x3498DB,
                "fields": [
                    {"name": "Timestamp", "value": timezone.localtime(timezone.now()).strftime("%Y-%m-%d %I:%M %p %Z"), "inline": True},
                    {"name": "Mode", "value": "dry-run" if dry_run else "live", "inline": True},
                ],
                "footer": {"text": "Trading Advisor — webhook connectivity check"},
            }
        ],
    }
    if dry_run:
        return {"action": "dry_run", "payload": payload}
    if not webhook_url:
        raise DiscordWebhookError("DISCORD_WEBHOOK_URL is not configured.")
    _post_discord(webhook_url=webhook_url, payload=payload)
    return {"action": "sent", "payload": payload}


def send_test_email_message(*, subject: str = "Trading Advisor test alert", body: str = "Email delivery wiring is working.", dry_run: bool = False) -> dict:
    recipient_list = _get_email_recipients()
    payload = {
        "subject": subject,
        "body": body,
        "to": recipient_list,
        "from_email": getattr(settings, "DEFAULT_FROM_EMAIL", "webmaster@localhost"),
    }
    if dry_run:
        return {"action": "dry_run", "payload": payload}
    if not recipient_list:
        raise EmailAlertError("ALERT_EMAIL_TO is not configured.")
    send_mail(subject=subject, message=body, from_email=payload["from_email"], recipient_list=recipient_list, fail_silently=False)
    return {"action": "sent", "payload": payload}


def get_enabled_delivery_channels() -> list[str]:
    channels: list[str] = []
    if bool(getattr(settings, "ALERT_DELIVERY_DISCORD_ENABLED", True)):
        channels.append(CHANNEL_DISCORD)
    if bool(getattr(settings, "ALERT_DELIVERY_EMAIL_ENABLED", False)):
        channels.append(CHANNEL_EMAIL)
    return channels


def get_alert_candidates(*, username: str | None = None):
    qs = (
        Signal.objects.select_related("instrument", "strategy", "trade_plan")
        .filter(status=Signal.Status.NEW, trade_plan__isnull=False)
        .exclude(direction=Signal.Direction.FLAT)
        .order_by("generated_at", "id")
    )
    max_age_minutes = int(getattr(settings, "ALERT_MAX_SIGNAL_AGE_MINUTES", 4320) or 4320)
    if max_age_minutes > 0:
        cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
        qs = qs.filter(generated_at__gte=cutoff)
    if username:
        qs = qs.filter(created_by__username=username)
    return qs


def _get_signal_price(signal: Signal) -> Decimal | None:
    """Best available price: trade plan entry price, or latest close bar."""
    if hasattr(signal, "trade_plan") and signal.trade_plan and signal.trade_plan.entry_price:
        return signal.trade_plan.entry_price
    bar = (
        PriceBar.objects.filter(instrument=signal.instrument, timeframe=signal.timeframe)
        .order_by("-ts")
        .values("close")
        .first()
    )
    return bar["close"] if bar else None


def _passes_price_filter(signal: Signal) -> bool:
    """Return False when the signal's price falls outside ALERT_MIN_PRICE / ALERT_MAX_PRICE."""
    min_raw = str(getattr(settings, "ALERT_MIN_PRICE", "") or "").strip()
    max_raw = str(getattr(settings, "ALERT_MAX_PRICE", "") or "").strip()
    if not min_raw and not max_raw:
        return True
    price = _get_signal_price(signal)
    if price is None:
        return True  # no price data available — allow through
    try:
        if min_raw and price < Decimal(min_raw):
            return False
        if max_raw and price > Decimal(max_raw):
            return False
    except Exception:  # noqa: BLE001
        return True  # misconfigured value — allow through rather than silently blocking alerts
    return True


def _passes_direction_filter(signal: Signal) -> bool:
    """Return False when signal direction is not in ALERT_DIRECTIONS (if set)."""
    raw = str(getattr(settings, "ALERT_DIRECTIONS", "") or "").strip().upper()
    if not raw:
        return True
    allowed = {d.strip() for d in raw.split(",") if d.strip()}
    return not allowed or signal.direction.upper() in allowed


def evaluate_signal_for_alert(*, signal: Signal) -> AlertDecision:
    if not hasattr(signal, "trade_plan"):
        return AlertDecision(False, "missing_trade_plan")

    plan = signal.trade_plan
    if not plan.suggested_qty or int(plan.suggested_qty) <= 0:
        return AlertDecision(False, "zero_qty")

    if not _passes_direction_filter(signal):
        return AlertDecision(False, "direction_filtered")

    if not _passes_price_filter(signal):
        return AlertDecision(False, "price_filtered")

    if not _passes_freshness(signal):
        return AlertDecision(False, "stale_signal")

    if not _passes_score_threshold(signal):
        return AlertDecision(False, "low_score")

    if signal.signal_kind == Signal.SignalKind.STATE and _should_skip_unchanged_state(signal):
        return AlertDecision(False, "unchanged_state")

    if not _passes_session_filter(signal):
        return AlertDecision(False, "outside_session")

    if _is_duplicate_success(signal):
        return AlertDecision(False, "already_sent")

    cooldown_minutes = int(getattr(settings, "ALERT_COOLDOWN_MINUTES", 30) or 30)
    if cooldown_minutes > 0 and _violates_symbol_cooldown(signal, cooldown_minutes=cooldown_minutes):
        return AlertDecision(False, "cooldown")

    max_per_day = int(getattr(settings, "ALERT_MAX_PER_DAY", 12) or 12)
    if max_per_day > 0 and _violates_daily_cap(signal, max_per_day=max_per_day):
        return AlertDecision(False, "daily_cap")

    return AlertDecision(True, "ok")


def explain_alert_eligibility(*, signal: Signal) -> AlertExplanation:
    decision = evaluate_signal_for_alert(signal=signal)
    score_value = _normalized_score_value(signal)
    score_threshold = _normalized_threshold(signal)
    score_gap = None
    if score_value is not None and score_threshold is not None:
        score_gap = round(score_value - score_threshold, 4)

    freshness_limit_minutes = int(getattr(settings, "ALERT_MAX_SIGNAL_AGE_MINUTES", 4320) or 4320)
    age_minutes = None
    if signal.generated_at:
        age_minutes = max(int((timezone.now() - signal.generated_at).total_seconds() // 60), 0)

    state_change_only = bool(getattr(settings, "ALERT_STATE_CHANGE_ONLY", True))
    unchanged_state = signal.signal_kind == Signal.SignalKind.STATE and _should_skip_unchanged_state(signal)
    session_ok = _passes_session_filter(signal)
    cooldown_minutes = int(getattr(settings, "ALERT_COOLDOWN_MINUTES", 30) or 30)
    max_per_day = int(getattr(settings, "ALERT_MAX_PER_DAY", 12) or 12)

    return AlertExplanation(
        eligible=decision.should_send,
        reason=decision.reason,
        score_value=score_value,
        score_threshold=score_threshold,
        score_gap=score_gap,
        age_minutes=age_minutes,
        freshness_limit_minutes=freshness_limit_minutes,
        session_ok=session_ok,
        session_window=f"{getattr(settings, 'EQUITY_ALERT_SESSION_START', '09:30')}–{getattr(settings, 'EQUITY_ALERT_SESSION_END', '16:00')} ET",
        unchanged_state=unchanged_state,
        state_change_only=state_change_only,
        duplicate_sent=_is_duplicate_success(signal),
        cooldown_hit=cooldown_minutes > 0 and _violates_symbol_cooldown(signal, cooldown_minutes=cooldown_minutes),
        daily_cap_hit=max_per_day > 0 and _violates_daily_cap(signal, max_per_day=max_per_day),
        price_filtered=not _passes_price_filter(signal),
    )


def build_tuning_preview(*, username: str | None = None, limit: int = 8):
    signals = list(
        get_alert_candidates(username=username)
        .select_related("instrument", "strategy")
        .order_by("-generated_at", "-score")[:limit]
    )
    preview: list[dict] = []
    for signal in signals:
        explanation = explain_alert_eligibility(signal=signal)
        preview.append(
            {
                "signal": signal,
                "explanation": explanation,
                "threshold_display": _display_threshold(explanation.score_threshold),
                "score_display": _display_score(explanation.score_value),
                "gap_display": _display_gap(explanation.score_gap),
            }
        )
    return preview


def build_next_session_queue(*, username: str | None = None, limit: int = 12):
    signals = list(
        get_alert_candidates(username=username)
        .select_related("instrument", "strategy")
        .order_by("-generated_at", "-score")[: max(limit * 4, limit)]
    )
    preview: list[dict] = []
    for signal in signals:
        if signal.instrument.asset_class == Instrument.AssetClass.CRYPTO:
            continue
        explanation = explain_alert_eligibility(signal=signal)
        if explanation.reason != "outside_session":
            continue
        preview.append(
            {
                "signal": signal,
                "explanation": explanation,
                "threshold_display": _display_threshold(explanation.score_threshold),
                "score_display": _display_score(explanation.score_value),
                "gap_display": _display_gap(explanation.score_gap),
            }
        )
    preview.sort(
        key=lambda item: (
            -(item["explanation"].score_gap if item["explanation"].score_gap is not None else -9999.0),
            -(float(item["signal"].score) if item["signal"].score is not None else -9999.0),
            -item["signal"].generated_at.timestamp(),
        )
    )
    return preview[:limit]


def build_alert_queue_preview(*, username: str | None = None, limit: int = 12):
    signals = list(
        get_alert_candidates(username=username)
        .select_related("instrument", "strategy")
        .order_by("-generated_at", "-score")[: max(limit * 3, limit)]
    )
    preview: list[dict] = []
    for signal in signals:
        explanation = explain_alert_eligibility(signal=signal)
        preview.append(
            {
                "signal": signal,
                "explanation": explanation,
                "threshold_display": _display_threshold(explanation.score_threshold),
                "score_display": _display_score(explanation.score_value),
                "gap_display": _display_gap(explanation.score_gap),
            }
        )
    preview.sort(
        key=lambda item: (
            0 if item["explanation"].eligible else 1,
            -(item["explanation"].score_gap if item["explanation"].score_gap is not None else -9999.0),
            -(float(item["signal"].score) if item["signal"].score is not None else -9999.0),
            -item["signal"].generated_at.timestamp(),
        )
    )
    return preview[:limit]


def deliver_enabled_alerts(*, signal: Signal, dry_run: bool = False) -> list[AlertOutcome]:
    outcomes: list[AlertOutcome] = []
    for channel in get_enabled_delivery_channels():
        if channel == CHANNEL_DISCORD:
            outcomes.append(deliver_discord_alert(signal=signal, dry_run=dry_run))
        elif channel == CHANNEL_EMAIL:
            outcomes.append(deliver_email_alert(signal=signal, dry_run=dry_run))
    return outcomes


def deliver_discord_alert(*, signal: Signal, dry_run: bool = False) -> AlertOutcome:
    decision = evaluate_signal_for_alert(signal=signal)
    payload = build_discord_payload(signal)
    if not decision.should_send:
        return _create_alert_outcome(signal=signal, channel=CHANNEL_DISCORD, status=AlertDelivery.Status.SKIPPED, reason=decision.reason, payload=payload, action="skipped")
    if dry_run:
        return _create_alert_outcome(signal=signal, channel=CHANNEL_DISCORD, status=AlertDelivery.Status.DRY_RUN, reason="dry_run", payload=payload, action="dry_run")
    webhook_url = getattr(settings, "DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return _create_alert_outcome(signal=signal, channel=CHANNEL_DISCORD, status=AlertDelivery.Status.FAILED, reason="missing_webhook", payload=payload, action="failed", error_message="DISCORD_WEBHOOK_URL is not configured.")
    try:
        _post_discord(webhook_url=webhook_url, payload=payload)
        return _create_alert_outcome(signal=signal, channel=CHANNEL_DISCORD, status=AlertDelivery.Status.SENT, reason="sent", payload=payload, action="sent", delivered_at=timezone.now())
    except Exception as exc:  # noqa: BLE001
        return _create_alert_outcome(signal=signal, channel=CHANNEL_DISCORD, status=AlertDelivery.Status.FAILED, reason="exception", payload=payload, action="failed", error_message=str(exc))


def deliver_email_alert(*, signal: Signal, dry_run: bool = False) -> AlertOutcome:
    decision = evaluate_signal_for_alert(signal=signal)
    payload = build_email_payload(signal)
    if not decision.should_send:
        return _create_alert_outcome(signal=signal, channel=CHANNEL_EMAIL, status=AlertDelivery.Status.SKIPPED, reason=decision.reason, payload=payload, action="skipped")
    if dry_run:
        return _create_alert_outcome(signal=signal, channel=CHANNEL_EMAIL, status=AlertDelivery.Status.DRY_RUN, reason="dry_run", payload=payload, action="dry_run")
    recipients = _get_email_recipients()
    if not recipients:
        return _create_alert_outcome(signal=signal, channel=CHANNEL_EMAIL, status=AlertDelivery.Status.FAILED, reason="missing_recipients", payload=payload, action="failed", error_message="ALERT_EMAIL_TO is not configured.")
    try:
        send_mail(
            subject=payload["subject"],
            message=payload["body"],
            from_email=payload["from_email"],
            recipient_list=recipients,
            fail_silently=False,
        )
        return _create_alert_outcome(signal=signal, channel=CHANNEL_EMAIL, status=AlertDelivery.Status.SENT, reason="sent", payload=payload, action="sent", delivered_at=timezone.now())
    except Exception as exc:  # noqa: BLE001
        return _create_alert_outcome(signal=signal, channel=CHANNEL_EMAIL, status=AlertDelivery.Status.FAILED, reason="exception", payload=payload, action="failed", error_message=str(exc))


def build_discord_payload(signal: Signal) -> dict:
    plan = signal.trade_plan
    instrument = signal.instrument
    ts_local = timezone.localtime(signal.generated_at)
    color = 0x2ECC71 if signal.direction == Signal.Direction.LONG else 0xE74C3C
    direction_emoji = "🟢" if signal.direction == Signal.Direction.LONG else "🔴"
    score_text = _display_score(_normalized_score_value(signal))
    qty_text = str(plan.suggested_qty) if plan.suggested_qty is not None else "n/a"
    risk_pct_display = (Decimal(plan.risk_per_trade_pct) * Decimal("100")).quantize(Decimal("0.01"))
    signal_type_text = signal.signal_label or signal.signal_kind
    # Position size and % of equity — available directly from TradePlan
    position_size_text = "n/a"
    if plan.entry_price is not None and plan.suggested_qty is not None:
        position_cost = (Decimal(plan.entry_price) * plan.suggested_qty).quantize(Decimal("0.01"))
        if plan.account_equity and plan.account_equity > 0:
            weight_pct = (position_cost / Decimal(plan.account_equity) * Decimal("100")).quantize(Decimal("0.1"))
            position_size_text = f"${position_cost:,.2f} ({weight_pct}% of equity)"
        else:
            position_size_text = f"${position_cost:,.2f}"
    content = f"{direction_emoji} {instrument.symbol} {signal.direction} — {signal.strategy.name} ({signal.timeframe}) [{signal_type_text}]"
    embed = {
        "title": f"{instrument.symbol} {signal.direction} setup",
        "description": signal.rationale or "Rule-based signal generated.",
        "color": color,
        "fields": [
            {"name": "Signal Type", "value": signal_type_text, "inline": True},
            {"name": "Score", "value": score_text, "inline": True},
            {"name": "Entry", "value": _fmt_price(plan.entry_price), "inline": True},
            {"name": "Stop", "value": _fmt_price(plan.stop_price), "inline": True},
            {"name": "Target 1", "value": _fmt_price(plan.target_1), "inline": True},
            {"name": "Target 2", "value": _fmt_price(plan.target_2), "inline": True},
            {"name": "Suggested Qty", "value": qty_text, "inline": True},
            {"name": "Position Size", "value": position_size_text, "inline": True},
            {"name": "Risk %", "value": f"{risk_pct_display}%", "inline": True},
            {"name": "Score Components", "value": _fmt_components(signal.score_components), "inline": False},
            {"name": "Time", "value": ts_local.strftime("%Y-%m-%d %I:%M %p %Z"), "inline": True},
            {"name": "Asset Class", "value": instrument.asset_class, "inline": True},
        ],
        "footer": {"text": "Trading Advisor — manual execution only"},
    }
    if plan.notes:
        embed["fields"].append({"name": "Plan Notes", "value": plan.notes[:1024], "inline": False})
    return {"content": content, "embeds": [embed]}


def build_email_payload(signal: Signal) -> dict:
    plan = signal.trade_plan
    instrument = signal.instrument
    signal_type_text = signal.signal_label or signal.signal_kind
    ts_local = timezone.localtime(signal.generated_at)
    score_text = _display_score(_normalized_score_value(signal))
    body = "\n".join([
        f"Symbol: {instrument.symbol}",
        f"Direction: {signal.direction}",
        f"Strategy: {signal.strategy.name}",
        f"Timeframe: {signal.timeframe}",
        f"Signal type: {signal_type_text}",
        f"Generated: {ts_local.strftime('%Y-%m-%d %I:%M %p %Z')}",
        f"Entry: {_fmt_price(plan.entry_price)}",
        f"Stop: {_fmt_price(plan.stop_price)}",
        f"Target 1: {_fmt_price(plan.target_1)}",
        f"Target 2: {_fmt_price(plan.target_2)}",
        f"Suggested Qty: {plan.suggested_qty if plan.suggested_qty is not None else 'n/a'}",
        f"Score: {score_text}",
        "",
        signal.rationale or "Rule-based signal generated.",
        "",
        "Trading Advisor — manual execution only.",
    ])
    return {
        "subject": f"Trading Advisor alert: {instrument.symbol} {signal.direction} {signal.timeframe}",
        "body": body,
        "from_email": getattr(settings, "DEFAULT_FROM_EMAIL", "webmaster@localhost"),
        "to": _get_email_recipients(),
    }


def _create_alert_outcome(*, signal: Signal, channel: str, status: str, reason: str, payload: dict, action: str, error_message: str = "", delivered_at=None) -> AlertOutcome:
    with transaction.atomic():
        delivery = AlertDelivery.objects.create(
            signal=signal,
            channel=channel,
            status=status,
            reason=reason,
            delivered_at=delivered_at,
            payload_snapshot=payload,
            error_message=error_message,
        )
    return AlertOutcome(delivery=delivery, action=action)


def _get_email_recipients() -> list[str]:
    raw = getattr(settings, "ALERT_EMAIL_TO", "") or ""
    return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]


def _post_discord(*, webhook_url: str, payload: dict) -> None:
    resp = requests.post(webhook_url, json=payload, timeout=15)
    if resp.status_code >= 400:
        raise DiscordWebhookError(f"Discord webhook failed ({resp.status_code}): {resp.text[:300]}")


def _passes_session_filter(signal: Signal) -> bool:
    if signal.instrument.asset_class == Instrument.AssetClass.CRYPTO:
        return True
    dt = timezone.localtime(timezone.now())
    if dt.weekday() >= 5:
        return False
    start = _parse_time(getattr(settings, "EQUITY_ALERT_SESSION_START", "09:30"))
    end = _parse_time(getattr(settings, "EQUITY_ALERT_SESSION_END", "16:00"))
    current = dt.timetz().replace(tzinfo=None)
    return start <= current <= end


def _passes_freshness(signal: Signal) -> bool:
    max_age_minutes = int(getattr(settings, "ALERT_MAX_SIGNAL_AGE_MINUTES", 4320) or 4320)
    if max_age_minutes <= 0:
        return True
    age = timezone.now() - signal.generated_at
    return age <= timedelta(minutes=max_age_minutes)


def _passes_score_threshold(signal: Signal) -> bool:
    score = _normalized_score_value(signal)
    if score is None:
        return True
    min_score = _normalized_threshold(signal)
    if min_score is None:
        return True
    return score >= min_score


def _should_skip_unchanged_state(signal: Signal) -> bool:
    if not bool(getattr(settings, "ALERT_STATE_CHANGE_ONLY", True)):
        return False
    prior = (
        Signal.objects.filter(
            created_by=signal.created_by,
            instrument=signal.instrument,
            strategy=signal.strategy,
            timeframe=signal.timeframe,
            signal_kind=Signal.SignalKind.STATE,
            generated_at__lt=signal.generated_at,
        )
        .order_by("-generated_at", "-id")
        .first()
    )
    if prior is None:
        return False
    return prior.direction == signal.direction and prior.signal_label == signal.signal_label


def _violates_symbol_cooldown(signal: Signal, *, cooldown_minutes: int) -> bool:
    cutoff = signal.generated_at - timedelta(minutes=cooldown_minutes)
    return AlertDelivery.objects.filter(
        signal__instrument=signal.instrument,
        status=AlertDelivery.Status.SENT,
        signal__generated_at__gte=cutoff,
        signal__generated_at__lt=signal.generated_at,
    ).exists()


def _violates_daily_cap(signal: Signal, *, max_per_day: int) -> bool:
    local_dt = timezone.localtime(signal.generated_at)
    day_start_local = datetime.combine(local_dt.date(), time.min, tzinfo=local_dt.tzinfo)
    day_end_local = day_start_local + timedelta(days=1)
    day_start_utc = day_start_local.astimezone(dt_timezone.utc)
    day_end_utc = day_end_local.astimezone(dt_timezone.utc)
    sent_count = AlertDelivery.objects.filter(
        status=AlertDelivery.Status.SENT,
        signal__generated_at__gte=day_start_utc,
        signal__generated_at__lt=day_end_utc,
    ).count()
    return sent_count >= max_per_day


def _is_duplicate_success(signal: Signal) -> bool:
    return AlertDelivery.objects.filter(signal=signal, status=AlertDelivery.Status.SENT).exists()


def _parse_time(raw: str) -> time:
    value = (raw or "09:30").strip()
    hour_str, minute_str = value.split(":", 1)
    return time(hour=int(hour_str), minute=int(minute_str))


def _fmt_price(value) -> str:
    if value is None:
        return "n/a"
    dec = Decimal(value)
    if abs(dec) >= Decimal("1000"):
        return f"{dec:,.2f}"
    if abs(dec) >= Decimal("1"):
        return f"{dec:,.4f}"
    return f"{dec:,.8f}"


def _fmt_components(components: dict | None) -> str:
    if not components:
        return "n/a"
    ordered = []
    for key in ["trend", "momentum", "volume", "volatility", "quality"]:
        if key in components:
            ordered.append(f"{key}: {float(components[key]):.2f}")
    for key, value in components.items():
        if key not in {"trend", "momentum", "volume", "volatility", "quality"}:
            ordered.append(f"{key}: {float(value):.2f}")
    return " | ".join(ordered)[:1024]


def _normalized_score_value(signal: Signal) -> float | None:
    if signal.score is None:
        return None
    score = float(signal.score)
    if score <= 1:
        score *= 100.0
    return round(score, 2)


def _normalized_threshold(signal: Signal) -> float | None:
    if signal.signal_kind == Signal.SignalKind.STATE:
        min_score = float(getattr(settings, "ALERT_MIN_SCORE_STATE", 0.60) or 0.60)
    else:
        min_score = float(getattr(settings, "ALERT_MIN_SCORE_EVENT", 0.80) or 0.80)
    if min_score <= 1:
        min_score *= 100.0
    return round(min_score, 2)


def _display_score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}/100"


def _display_threshold(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}/100"


def _display_gap(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}"
