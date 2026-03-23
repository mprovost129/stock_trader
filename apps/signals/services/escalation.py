from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.portfolios.models import PortfolioHealthSnapshot
from apps.signals.models import AlertDelivery, OperatorNotification, Signal
from apps.signals.services.alerts import _get_email_recipients, _post_discord, get_enabled_delivery_channels
from apps.signals.services.delivery_health import get_delivery_health_summary


@dataclass(frozen=True)
class EscalationResult:
    channel: str
    status: str
    reason: str


@dataclass(frozen=True)
class EscalationRunSummary:
    triggered: bool
    reason: str
    headline: str
    results: list[EscalationResult]


def check_and_send_delivery_health_escalation(*, dry_run: bool = False) -> EscalationRunSummary:
    summary = get_delivery_health_summary()
    problems = _collect_problems(summary)
    if not problems:
        return EscalationRunSummary(triggered=False, reason="healthy", headline="No delivery-health escalation needed", results=[])

    cooldown_minutes = int(getattr(settings, "ALERT_ESCALATION_COOLDOWN_MINUTES", 180) or 180)
    if _cooldown_active(kind=OperatorNotification.Kind.DELIVERY_HEALTH, cooldown_minutes=cooldown_minutes):
        return EscalationRunSummary(triggered=False, reason="cooldown", headline="Escalation suppressed by cooldown", results=[])

    headline = _build_headline(summary=summary, problems=problems)
    body = _build_body(summary=summary, problems=problems)
    results = _deliver_operator_notification(
        kind=OperatorNotification.Kind.DELIVERY_HEALTH,
        headline=headline,
        body=body,
        dry_run=dry_run,
    )
    return EscalationRunSummary(triggered=True, reason="triggered", headline=headline, results=results)


def check_and_send_delivery_recovery_notification(*, dry_run: bool = False) -> EscalationRunSummary:
    summary = get_delivery_health_summary()
    problems = _collect_problems(summary)
    if problems:
        return EscalationRunSummary(triggered=False, reason="still_unhealthy", headline="Delivery health still degraded", results=[])
    if not _has_open_delivery_health_incident():
        return EscalationRunSummary(triggered=False, reason="no_open_incident", headline="No open delivery-health incident to resolve", results=[])

    cooldown_minutes = int(getattr(settings, "ALERT_RECOVERY_COOLDOWN_MINUTES", 60) or 60)
    if _cooldown_active(kind=OperatorNotification.Kind.DELIVERY_RECOVERY, cooldown_minutes=cooldown_minutes):
        return EscalationRunSummary(triggered=False, reason="cooldown", headline="Recovery notification suppressed by cooldown", results=[])

    headline = "Trading Advisor recovery: automatic alert delivery looks healthy again"
    body = _build_recovery_body(summary=summary)
    results = _deliver_operator_notification(
        kind=OperatorNotification.Kind.DELIVERY_RECOVERY,
        headline=headline,
        body=body,
        dry_run=dry_run,
    )
    return EscalationRunSummary(triggered=True, reason="triggered", headline=headline, results=results)


def check_and_send_portfolio_health_notification(*, user, dry_run: bool = False) -> EscalationRunSummary:
    snapshots = list(
        PortfolioHealthSnapshot.objects.filter(user=user).order_by("-created_at")[:2]
    )
    if len(snapshots) < 2:
        return EscalationRunSummary(
            triggered=False,
            reason="insufficient_history",
            headline="No portfolio health deterioration check possible: need at least two snapshots",
            results=[],
        )

    latest, previous = snapshots[0], snapshots[1]
    threshold = int(getattr(settings, "PORTFOLIO_HEALTH_DETERIORATION_THRESHOLD", 10) or 10)
    score_drop = previous.overall_score - latest.overall_score
    grade_deteriorated = (
        latest.overall_grade_code in ("ACTION", "CRITICAL")
        and previous.overall_grade_code not in ("ACTION", "CRITICAL")
    )

    if score_drop < threshold and not grade_deteriorated:
        return EscalationRunSummary(
            triggered=False,
            reason="healthy",
            headline="No portfolio health deterioration detected",
            results=[],
        )

    cooldown_minutes = int(getattr(settings, "PORTFOLIO_HEALTH_ALERT_COOLDOWN_MINUTES", 120) or 120)
    if _cooldown_active(kind=OperatorNotification.Kind.PORTFOLIO_HEALTH, cooldown_minutes=cooldown_minutes):
        return EscalationRunSummary(
            triggered=False,
            reason="cooldown",
            headline="Portfolio health notification suppressed by cooldown",
            results=[],
        )

    reason_parts: list[str] = []
    if score_drop >= threshold:
        reason_parts.append(f"score dropped {score_drop} points ({previous.overall_score} -> {latest.overall_score})")
    if grade_deteriorated:
        reason_parts.append(f"grade moved to {latest.overall_grade_label or latest.overall_grade_code}")

    headline = f"Trading Advisor: portfolio health deteriorated for {user.username}"
    body = _build_portfolio_health_body(latest=latest, previous=previous, reason_parts=reason_parts)
    results = _deliver_operator_notification(
        kind=OperatorNotification.Kind.PORTFOLIO_HEALTH,
        headline=headline,
        body=body,
        dry_run=dry_run,
    )
    return EscalationRunSummary(triggered=True, reason="triggered", headline=headline, results=results)


def notify_scheduler_failure(*, iteration: int, error: str, dry_run: bool = False) -> EscalationRunSummary:
    headline = f"Trading Advisor: scheduler cycle {iteration} failed"
    body = f"Cycle {iteration} raised an exception and was skipped.\n\nError:\n{error[:1800]}"
    results = _deliver_operator_notification(
        kind=OperatorNotification.Kind.SCHEDULER_FAILURE,
        headline=headline,
        body=body,
        dry_run=dry_run,
    )
    return EscalationRunSummary(triggered=True, reason="scheduler_error", headline=headline, results=results)


def check_and_send_daily_alert_digest(*, username: str | None = None, dry_run: bool = False, now=None) -> EscalationRunSummary:
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    if local_now.weekday() >= 5:
        return EscalationRunSummary(
            triggered=False,
            reason="weekend",
            headline="Daily close digest skipped on weekend",
            results=[],
        )

    close_time = _parse_hhmm(getattr(settings, "EQUITY_ALERT_SESSION_END", "16:00"))
    if local_now.timetz().replace(tzinfo=None) < close_time:
        return EscalationRunSummary(
            triggered=False,
            reason="before_close",
            headline="Daily close digest waits for market close",
            results=[],
        )

    day_start_local = datetime.combine(local_now.date(), dt_time.min, tzinfo=local_now.tzinfo)
    day_end_local = day_start_local + timedelta(days=1)
    day_start_utc = day_start_local.astimezone(timezone.UTC)
    day_end_utc = day_end_local.astimezone(timezone.UTC)

    digest_scope = (username or "all users").strip()
    digest_key = f"{local_now.date().isoformat()}|{digest_scope}"
    if _already_sent_daily_digest(digest_key=digest_key):
        return EscalationRunSummary(
            triggered=False,
            reason="already_sent_today",
            headline=f"Daily close digest already sent for {local_now.date().isoformat()} ({digest_scope})",
            results=[],
        )

    base_qs = AlertDelivery.objects.filter(created_at__gte=day_start_utc, created_at__lt=day_end_utc).select_related("signal", "signal__instrument")
    if username:
        base_qs = base_qs.filter(signal__created_by__username=username)

    total = base_qs.count()
    status_counts = Counter(base_qs.values_list("status", flat=True))
    reason_counts = Counter(base_qs.values_list("reason", flat=True))
    sent_qs = base_qs.filter(status=AlertDelivery.Status.SENT).order_by("-signal__score", "-created_at")[:5]
    sent_examples = [
        f"{item.signal.instrument.symbol} {item.signal.direction} {item.signal.timeframe} score={float(item.signal.score or 0):.2f}/100"
        for item in sent_qs
    ]
    skipped_reasons = Counter(
        base_qs.filter(status=AlertDelivery.Status.SKIPPED).values_list("reason", flat=True)
    )
    failed_reasons = Counter(
        base_qs.filter(status=AlertDelivery.Status.FAILED).values_list("reason", flat=True)
    )

    signal_qs = Signal.objects.filter(created_at__gte=day_start_utc, created_at__lt=day_end_utc)
    if username:
        signal_qs = signal_qs.filter(created_by__username=username)
    new_signal_count = signal_qs.count()

    headline = f"Trading Advisor close digest: {local_now.date().isoformat()} ({digest_scope})"
    body = _build_daily_digest_body(
        username=username,
        local_now=local_now,
        total=total,
        status_counts=status_counts,
        reason_counts=reason_counts,
        skipped_reasons=skipped_reasons,
        failed_reasons=failed_reasons,
        sent_examples=sent_examples,
        new_signal_count=new_signal_count,
    )
    results = _deliver_operator_notification(
        kind=OperatorNotification.Kind.DAILY_ALERT_DIGEST,
        headline=headline,
        body=body,
        dry_run=dry_run,
        digest_key=digest_key,
    )
    return EscalationRunSummary(triggered=True, reason="triggered", headline=headline, results=results)


def _collect_problems(summary) -> list[str]:
    problems: list[str] = []
    if summary.in_drought:
        problems.append(summary.drought_headline)
    for item in summary.channels:
        if not item.enabled:
            continue
        if item.failure_streak >= summary.failure_streak_threshold:
            problems.append(f"{item.channel} failure streak is {item.failure_streak}")
        elif item.failed_count_window > 0 and item.sent_count_window == 0:
            problems.append(f"{item.channel} has failures in the last {summary.window_hours}h with no successful sends")
    return problems


def _build_headline(*, summary, problems: list[str]) -> str:
    if summary.in_drought and problems:
        return f"Trading Advisor escalation: delivery drought + channel risk ({len(problems)} issue{'s' if len(problems) != 1 else ''})"
    return f"Trading Advisor escalation: delivery health issue ({len(problems)} issue{'s' if len(problems) != 1 else ''})"


def _build_body(*, summary, problems: list[str]) -> str:
    lines = [
        "Automatic alert delivery needs attention.",
        "",
        f"Drought status: {summary.drought_headline}",
        f"Health window: {summary.window_hours}h",
        f"Recent failures: {summary.total_recent_failures}",
        "",
        "Detected problems:",
    ]
    lines.extend([f"- {problem}" for problem in problems])
    lines.append("")
    lines.append("Channel summary:")
    for item in summary.channels:
        lines.append(
            f"- {item.channel}: enabled={item.enabled} sent={item.sent_count_window} failed={item.failed_count_window} streak={item.failure_streak} headline={item.headline}"
        )
    lines.append("")
    lines.append("Recommended actions:")
    lines.append("- Run python manage.py check_alert_delivery_health")
    lines.append("- Run python manage.py send_test_alert")
    lines.append("- Verify Discord webhook and email backend settings")
    return "\n".join(lines)


def _build_recovery_body(*, summary) -> str:
    lines = [
        "Automatic alert delivery no longer shows an active drought or channel-health incident.",
        "",
        f"Drought status: {summary.drought_headline}",
        f"Health window: {summary.window_hours}h",
        f"Recent failures in window: {summary.total_recent_failures}",
        "",
        "Current channel summary:",
    ]
    for item in summary.channels:
        lines.append(
            f"- {item.channel}: enabled={item.enabled} sent={item.sent_count_window} failed={item.failed_count_window} streak={item.failure_streak} headline={item.headline}"
        )
    lines.append("")
    lines.append("Recommended follow-through:")
    lines.append("- Confirm the last send_test_alert reached the intended channels")
    lines.append("- Leave delivery-health checks enabled in the scheduler")
    return "\n".join(lines)


def _build_portfolio_health_body(*, latest, previous, reason_parts: list[str]) -> str:
    lines = [
        "Portfolio health has deteriorated since the last snapshot.",
        "",
        f"Previous snapshot: score={previous.overall_score} grade={previous.overall_grade_label or previous.overall_grade_code} urgent={previous.urgent_count} attention={previous.attention_count}",
        f"Latest snapshot:   score={latest.overall_score} grade={latest.overall_grade_label or latest.overall_grade_code} urgent={latest.urgent_count} attention={latest.attention_count}",
        "",
        "Detected changes:",
    ]
    lines.extend([f"- {r}" for r in reason_parts])
    if latest.weakest_account_label:
        lines.append("")
        lines.append(f"Weakest account: {latest.weakest_account_label} (score={latest.weakest_account_score})")
    lines.append("")
    lines.append("Recommended actions:")
    lines.append("- Review the Portfolio Health Score page in Allocation Controls")
    lines.append("- Check stop-policy queue and holding queues for items needing attention")
    lines.append("- Run python manage.py save_portfolio_health_snapshot --username <user> to refresh")
    return "\n".join(lines)


def _build_daily_digest_body(
    *,
    username: str | None,
    local_now,
    total: int,
    status_counts: Counter,
    reason_counts: Counter,
    skipped_reasons: Counter,
    failed_reasons: Counter,
    sent_examples: list[str],
    new_signal_count: int,
) -> str:
    scope = username or "all users"
    lines = [
        "End-of-day alert delivery digest.",
        "",
        f"Scope: {scope}",
        f"Date: {local_now.date().isoformat()} ({local_now.tzname()})",
        f"Signals generated today: {new_signal_count}",
        f"Alert delivery attempts today: {total}",
        f"- SENT: {status_counts.get('SENT', 0)}",
        f"- SKIPPED: {status_counts.get('SKIPPED', 0)}",
        f"- FAILED: {status_counts.get('FAILED', 0)}",
        f"- DRY_RUN: {status_counts.get('DRY_RUN', 0)}",
        "",
        "Why alerts came through:",
        "- SENT alerts passed all policy gates (qty, freshness, score threshold, session window, cooldown, and daily cap).",
    ]
    if sent_examples:
        lines.append(f"- Top sent examples: {'; '.join(sent_examples)}")
    else:
        lines.append("- No alerts were sent today.")

    lines.append("")
    lines.append("Why alerts were skipped:")
    if skipped_reasons:
        for reason, count in skipped_reasons.most_common(8):
            lines.append(f"- {reason}: {count}")
    else:
        lines.append("- No skipped alerts today.")

    if failed_reasons:
        lines.append("")
        lines.append("Delivery failures:")
        for reason, count in failed_reasons.most_common(5):
            lines.append(f"- {reason}: {count}")

    if reason_counts:
        lines.append("")
        lines.append("Top overall reasons:")
        for reason, count in reason_counts.most_common(6):
            lines.append(f"- {reason}: {count}")

    lines.append("")
    lines.append("Manual check:")
    lines.append("- python manage.py preview_alert_queue --username <your_username>")
    lines.append("- python manage.py system_health --username <your_username>")
    return "\n".join(lines)


def _cooldown_active(*, kind: str, cooldown_minutes: int) -> bool:
    if cooldown_minutes <= 0:
        return False
    cutoff = timezone.now() - timedelta(minutes=cooldown_minutes)
    return OperatorNotification.objects.filter(
        kind=kind,
        status=OperatorNotification.Status.SENT,
        created_at__gte=cutoff,
    ).exists()


def _has_open_delivery_health_incident() -> bool:
    last_escalation = (
        OperatorNotification.objects.filter(
            kind=OperatorNotification.Kind.DELIVERY_HEALTH,
            status=OperatorNotification.Status.SENT,
        )
        .order_by("-created_at")
        .first()
    )
    if not last_escalation:
        return False
    last_recovery = (
        OperatorNotification.objects.filter(
            kind=OperatorNotification.Kind.DELIVERY_RECOVERY,
            status=OperatorNotification.Status.SENT,
        )
        .order_by("-created_at")
        .first()
    )
    if not last_recovery:
        return True
    return last_escalation.created_at > last_recovery.created_at


def _already_sent_daily_digest(*, digest_key: str) -> bool:
    if not digest_key:
        return False
    return OperatorNotification.objects.filter(
        kind=OperatorNotification.Kind.DAILY_ALERT_DIGEST,
        status=OperatorNotification.Status.SENT,
        reason=digest_key,
    ).exists()


def _deliver_operator_notification(*, kind: str, headline: str, body: str, dry_run: bool, digest_key: str = "") -> list[EscalationResult]:
    enabled_channels = get_enabled_delivery_channels()
    results: list[EscalationResult] = []
    reason_key = digest_key or ""
    if not enabled_channels:
        _create_notification(
            kind=kind,
            channel=OperatorNotification.Channel.EMAIL,
            status=OperatorNotification.Status.SKIPPED,
            reason=reason_key or "no_enabled_channels",
            headline=headline,
            body=body,
            payload_snapshot={},
            error_message="No enabled delivery channels are configured.",
        )
        return [EscalationResult(channel="NONE", status=OperatorNotification.Status.SKIPPED, reason="no_enabled_channels")]

    if OperatorNotification.Channel.DISCORD in enabled_channels:
        results.append(_send_discord_notification(kind=kind, headline=headline, body=body, dry_run=dry_run, digest_key=reason_key))
    if OperatorNotification.Channel.EMAIL in enabled_channels:
        results.append(_send_email_notification(kind=kind, headline=headline, body=body, dry_run=dry_run, digest_key=reason_key))
    return results


def _send_discord_notification(*, kind: str, headline: str, body: str, dry_run: bool, digest_key: str = "") -> EscalationResult:
    if kind == OperatorNotification.Kind.PORTFOLIO_HEALTH:
        content = "Trading Advisor: portfolio health alert"
        color = 0xE74C3C
    elif kind == OperatorNotification.Kind.DELIVERY_HEALTH:
        content = "Trading Advisor operator escalation"
        color = 0xE67E22
    elif kind == OperatorNotification.Kind.DAILY_ALERT_DIGEST:
        content = "Trading Advisor close digest"
        color = 0x3498DB
    else:
        content = "Trading Advisor operator recovery"
        color = 0x2ECC71
    payload = {
        "content": content,
        "embeds": [
            {
                "title": headline,
                "description": body[:4096],
                "color": color,
                "footer": {"text": "Trading Advisor - unattended operator notice"},
            }
        ],
    }
    if dry_run:
        _create_notification(kind=kind, channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.DRY_RUN, reason=digest_key or "dry_run", headline=headline, body=body, payload_snapshot=payload)
        return EscalationResult(channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.DRY_RUN, reason="dry_run")
    webhook_url = getattr(settings, "DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        _create_notification(kind=kind, channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.FAILED, reason=digest_key or "missing_webhook", headline=headline, body=body, payload_snapshot=payload, error_message="DISCORD_WEBHOOK_URL is not configured.")
        return EscalationResult(channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.FAILED, reason="missing_webhook")
    try:
        _post_discord(webhook_url=webhook_url, payload=payload)
        _create_notification(kind=kind, channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.SENT, reason=digest_key or "sent", headline=headline, body=body, payload_snapshot=payload, delivered_at=timezone.now())
        return EscalationResult(channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.SENT, reason="sent")
    except Exception as exc:  # noqa: BLE001
        _create_notification(kind=kind, channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.FAILED, reason=digest_key or "exception", headline=headline, body=body, payload_snapshot=payload, error_message=str(exc))
        return EscalationResult(channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.FAILED, reason="exception")


def _send_email_notification(*, kind: str, headline: str, body: str, dry_run: bool, digest_key: str = "") -> EscalationResult:
    recipients = _get_email_recipients()
    payload = {
        "subject": headline,
        "body": body,
        "to": recipients,
        "from_email": getattr(settings, "DEFAULT_FROM_EMAIL", "webmaster@localhost"),
    }
    if dry_run:
        _create_notification(kind=kind, channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.DRY_RUN, reason=digest_key or "dry_run", headline=headline, body=body, payload_snapshot=payload)
        return EscalationResult(channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.DRY_RUN, reason="dry_run")
    if not recipients:
        _create_notification(kind=kind, channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.FAILED, reason=digest_key or "missing_recipients", headline=headline, body=body, payload_snapshot=payload, error_message="ALERT_EMAIL_TO is not configured.")
        return EscalationResult(channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.FAILED, reason="missing_recipients")
    try:
        send_mail(subject=headline, message=body, from_email=payload["from_email"], recipient_list=recipients, fail_silently=False)
        _create_notification(kind=kind, channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.SENT, reason=digest_key or "sent", headline=headline, body=body, payload_snapshot=payload, delivered_at=timezone.now())
        return EscalationResult(channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.SENT, reason="sent")
    except Exception as exc:  # noqa: BLE001
        _create_notification(kind=kind, channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.FAILED, reason=digest_key or "exception", headline=headline, body=body, payload_snapshot=payload, error_message=str(exc))
        return EscalationResult(channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.FAILED, reason="exception")


def _create_notification(*, kind: str, channel: str, status: str, reason: str, headline: str, body: str, payload_snapshot: dict, error_message: str = "", delivered_at=None) -> OperatorNotification:
    return OperatorNotification.objects.create(
        kind=kind,
        channel=channel,
        status=status,
        reason=reason,
        headline=headline,
        body=body,
        delivered_at=delivered_at,
        payload_snapshot=payload_snapshot,
        error_message=error_message,
    )


def _parse_hhmm(value: str) -> dt_time:
    hour_str, minute_str = (value or "16:00").split(":", 1)
    return dt_time(hour=int(hour_str), minute=int(minute_str))
