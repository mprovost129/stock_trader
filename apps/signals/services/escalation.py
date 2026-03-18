from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.portfolios.models import PortfolioHealthSnapshot
from apps.signals.models import OperatorNotification
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
    """Compare the two most recent portfolio health snapshots and notify if score deteriorated."""
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
        reason_parts.append(f"score dropped {score_drop} points ({previous.overall_score} → {latest.overall_score})")
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
    """Send a Discord/email notification when a scheduler cycle raises an unhandled exception."""
    headline = f"Trading Advisor: scheduler cycle {iteration} failed"
    body = f"Cycle {iteration} raised an exception and was skipped.\n\nError:\n{error[:1800]}"
    results = _deliver_operator_notification(
        kind=OperatorNotification.Kind.SCHEDULER_FAILURE,
        headline=headline,
        body=body,
        dry_run=dry_run,
    )
    return EscalationRunSummary(triggered=True, reason="scheduler_error", headline=headline, results=results)


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


def _deliver_operator_notification(*, kind: str, headline: str, body: str, dry_run: bool) -> list[EscalationResult]:
    enabled_channels = get_enabled_delivery_channels()
    results: list[EscalationResult] = []
    if not enabled_channels:
        _create_notification(
            kind=kind,
            channel=OperatorNotification.Channel.EMAIL,
            status=OperatorNotification.Status.SKIPPED,
            reason="no_enabled_channels",
            headline=headline,
            body=body,
            payload_snapshot={},
            error_message="No enabled delivery channels are configured.",
        )
        return [EscalationResult(channel="NONE", status=OperatorNotification.Status.SKIPPED, reason="no_enabled_channels")]

    if OperatorNotification.Channel.DISCORD in enabled_channels:
        results.append(_send_discord_notification(kind=kind, headline=headline, body=body, dry_run=dry_run))
    if OperatorNotification.Channel.EMAIL in enabled_channels:
        results.append(_send_email_notification(kind=kind, headline=headline, body=body, dry_run=dry_run))
    return results


def _send_discord_notification(*, kind: str, headline: str, body: str, dry_run: bool) -> EscalationResult:
    if kind == OperatorNotification.Kind.PORTFOLIO_HEALTH:
        content = "📉 Trading Advisor: portfolio health alert"
        color = 0xE74C3C
    elif kind == OperatorNotification.Kind.DELIVERY_HEALTH:
        content = "🚨 Trading Advisor operator escalation"
        color = 0xE67E22
    else:
        content = "✅ Trading Advisor operator recovery"
        color = 0x2ECC71
    payload = {
        "content": content,
        "embeds": [
            {
                "title": headline,
                "description": body[:4096],
                "color": color,
                "footer": {"text": "Trading Advisor — unattended operator notice"},
            }
        ],
    }
    if dry_run:
        _create_notification(kind=kind, channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.DRY_RUN, reason="dry_run", headline=headline, body=body, payload_snapshot=payload)
        return EscalationResult(channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.DRY_RUN, reason="dry_run")
    webhook_url = getattr(settings, "DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        _create_notification(kind=kind, channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.FAILED, reason="missing_webhook", headline=headline, body=body, payload_snapshot=payload, error_message="DISCORD_WEBHOOK_URL is not configured.")
        return EscalationResult(channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.FAILED, reason="missing_webhook")
    try:
        _post_discord(webhook_url=webhook_url, payload=payload)
        _create_notification(kind=kind, channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.SENT, reason="sent", headline=headline, body=body, payload_snapshot=payload, delivered_at=timezone.now())
        return EscalationResult(channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.SENT, reason="sent")
    except Exception as exc:  # noqa: BLE001
        _create_notification(kind=kind, channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.FAILED, reason="exception", headline=headline, body=body, payload_snapshot=payload, error_message=str(exc))
        return EscalationResult(channel=OperatorNotification.Channel.DISCORD, status=OperatorNotification.Status.FAILED, reason="exception")


def _send_email_notification(*, kind: str, headline: str, body: str, dry_run: bool) -> EscalationResult:
    recipients = _get_email_recipients()
    payload = {
        "subject": headline,
        "body": body,
        "to": recipients,
        "from_email": getattr(settings, "DEFAULT_FROM_EMAIL", "webmaster@localhost"),
    }
    if dry_run:
        _create_notification(kind=kind, channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.DRY_RUN, reason="dry_run", headline=headline, body=body, payload_snapshot=payload)
        return EscalationResult(channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.DRY_RUN, reason="dry_run")
    if not recipients:
        _create_notification(kind=kind, channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.FAILED, reason="missing_recipients", headline=headline, body=body, payload_snapshot=payload, error_message="ALERT_EMAIL_TO is not configured.")
        return EscalationResult(channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.FAILED, reason="missing_recipients")
    try:
        send_mail(subject=headline, message=body, from_email=payload["from_email"], recipient_list=recipients, fail_silently=False)
        _create_notification(kind=kind, channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.SENT, reason="sent", headline=headline, body=body, payload_snapshot=payload, delivered_at=timezone.now())
        return EscalationResult(channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.SENT, reason="sent")
    except Exception as exc:  # noqa: BLE001
        _create_notification(kind=kind, channel=OperatorNotification.Channel.EMAIL, status=OperatorNotification.Status.FAILED, reason="exception", headline=headline, body=body, payload_snapshot=payload, error_message=str(exc))
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
