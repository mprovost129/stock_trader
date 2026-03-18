from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.signals.models import AlertDelivery


@dataclass(frozen=True)
class ChannelHealth:
    channel: str
    enabled: bool
    sent_count_window: int
    failed_count_window: int
    skipped_count_window: int
    dry_run_count_window: int
    last_attempt_at: object | None
    last_success_at: object | None
    last_failure_at: object | None
    failure_streak: int
    healthy: bool
    headline: str


@dataclass(frozen=True)
class DeliveryHealthSummary:
    window_hours: int
    drought_minutes: int
    failure_streak_threshold: int
    generated_at: object
    total_recent_failures: int
    latest_attempt_at: object | None
    latest_success_at: object | None
    in_drought: bool
    drought_headline: str
    channels: list[ChannelHealth]


CHANNELS = [
    AlertDelivery.Channel.DISCORD,
    AlertDelivery.Channel.EMAIL,
]


def get_delivery_health_summary() -> DeliveryHealthSummary:
    now = timezone.now()
    window_hours = int(getattr(settings, "ALERT_DELIVERY_HEALTH_WINDOW_HOURS", 24) or 24)
    drought_minutes = int(getattr(settings, "ALERT_DROUGHT_MINUTES", 240) or 240)
    failure_streak_threshold = int(getattr(settings, "ALERT_FAILURE_STREAK_THRESHOLD", 3) or 3)
    window_start = now - timedelta(hours=max(window_hours, 1))

    latest_attempt = AlertDelivery.objects.order_by("-created_at").first()
    latest_success = (
        AlertDelivery.objects.filter(status=AlertDelivery.Status.SENT)
        .order_by("-created_at")
        .first()
    )

    channels: list[ChannelHealth] = []
    for channel in CHANNELS:
        enabled = channel in _get_enabled_channels()
        recent_qs = AlertDelivery.objects.filter(channel=channel, created_at__gte=window_start).order_by("-created_at")
        sent_count_window = recent_qs.filter(status=AlertDelivery.Status.SENT).count()
        failed_count_window = recent_qs.filter(status=AlertDelivery.Status.FAILED).count()
        skipped_count_window = recent_qs.filter(status=AlertDelivery.Status.SKIPPED).count()
        dry_run_count_window = recent_qs.filter(status=AlertDelivery.Status.DRY_RUN).count()

        latest_channel_attempt = AlertDelivery.objects.filter(channel=channel).order_by("-created_at").first()
        latest_channel_success = (
            AlertDelivery.objects.filter(channel=channel, status=AlertDelivery.Status.SENT)
            .order_by("-created_at")
            .first()
        )
        latest_channel_failure = (
            AlertDelivery.objects.filter(channel=channel, status=AlertDelivery.Status.FAILED)
            .order_by("-created_at")
            .first()
        )
        failure_streak = _get_failure_streak(channel)
        healthy, headline = _build_channel_status(
            enabled=enabled,
            sent_count_window=sent_count_window,
            failed_count_window=failed_count_window,
            failure_streak=failure_streak,
            failure_streak_threshold=failure_streak_threshold,
            last_attempt_at=latest_channel_attempt.created_at if latest_channel_attempt else None,
            last_success_at=latest_channel_success.created_at if latest_channel_success else None,
        )
        channels.append(
            ChannelHealth(
                channel=channel,
                enabled=enabled,
                sent_count_window=sent_count_window,
                failed_count_window=failed_count_window,
                skipped_count_window=skipped_count_window,
                dry_run_count_window=dry_run_count_window,
                last_attempt_at=latest_channel_attempt.created_at if latest_channel_attempt else None,
                last_success_at=latest_channel_success.created_at if latest_channel_success else None,
                last_failure_at=latest_channel_failure.created_at if latest_channel_failure else None,
                failure_streak=failure_streak,
                healthy=healthy,
                headline=headline,
            )
        )

    in_drought, drought_headline = _build_drought_status(
        latest_success_at=latest_success.created_at if latest_success else None,
        latest_attempt_at=latest_attempt.created_at if latest_attempt else None,
        drought_minutes=drought_minutes,
        now=now,
    )

    total_recent_failures = AlertDelivery.objects.filter(
        status=AlertDelivery.Status.FAILED,
        created_at__gte=window_start,
    ).count()

    return DeliveryHealthSummary(
        window_hours=window_hours,
        drought_minutes=drought_minutes,
        failure_streak_threshold=failure_streak_threshold,
        generated_at=now,
        total_recent_failures=total_recent_failures,
        latest_attempt_at=latest_attempt.created_at if latest_attempt else None,
        latest_success_at=latest_success.created_at if latest_success else None,
        in_drought=in_drought,
        drought_headline=drought_headline,
        channels=channels,
    )


def _get_enabled_channels() -> list[str]:
    channels: list[str] = []
    if bool(getattr(settings, "ALERT_DELIVERY_DISCORD_ENABLED", True)):
        channels.append(AlertDelivery.Channel.DISCORD)
    if bool(getattr(settings, "ALERT_DELIVERY_EMAIL_ENABLED", False)):
        channels.append(AlertDelivery.Channel.EMAIL)
    return channels


def _get_failure_streak(channel: str) -> int:
    streak = 0
    for delivery in AlertDelivery.objects.filter(channel=channel).order_by("-created_at")[:20]:
        if delivery.status == AlertDelivery.Status.FAILED:
            streak += 1
            continue
        break
    return streak


def _build_channel_status(*, enabled: bool, sent_count_window: int, failed_count_window: int, failure_streak: int, failure_streak_threshold: int, last_attempt_at, last_success_at):
    if not enabled:
        return True, "Channel disabled"
    if last_attempt_at is None:
        return False, "Enabled but never tested"
    if failure_streak_threshold > 0 and failure_streak >= failure_streak_threshold:
        return False, f"Failure streak {failure_streak}"
    if failed_count_window > 0 and sent_count_window == 0:
        return False, "Failures without a recent success"
    if last_success_at is None:
        return False, "No successful delivery yet"
    return True, "Healthy"


def _build_drought_status(*, latest_success_at, latest_attempt_at, drought_minutes: int, now):
    if latest_success_at is None:
        if latest_attempt_at is None:
            return True, "No alert attempts have been recorded yet"
        return True, "No successful alert delivery has been recorded yet"
    age = now - latest_success_at
    if drought_minutes > 0 and age > timedelta(minutes=drought_minutes):
        mins = int(age.total_seconds() // 60)
        return True, f"Last successful alert was {mins} minutes ago"
    mins = int(age.total_seconds() // 60)
    return False, f"Last successful alert was {mins} minutes ago"
