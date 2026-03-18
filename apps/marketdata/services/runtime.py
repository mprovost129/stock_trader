from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.utils import timezone

MARKET_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class RuntimeMode:
    name: str
    market_open: bool
    reason: str
    local_dt: datetime


def current_market_time() -> datetime:
    return timezone.now().astimezone(MARKET_TZ)


def is_equity_market_open_now(*, now: datetime | None = None, start: str = "09:30", end: str = "16:00") -> bool:
    local_now = (now or current_market_time()).astimezone(MARKET_TZ)
    if local_now.weekday() >= 5:
        return False
    start_time = _parse_hhmm(start)
    end_time = _parse_hhmm(end)
    local_time = local_now.time().replace(tzinfo=None)
    return start_time <= local_time <= end_time


def classify_runtime_mode(*, now: datetime | None = None, start: str = "09:30", end: str = "16:00") -> RuntimeMode:
    local_now = (now or current_market_time()).astimezone(MARKET_TZ)
    if local_now.weekday() >= 5:
        return RuntimeMode(name="CLOSED", market_open=False, reason="weekend", local_dt=local_now)
    if is_equity_market_open_now(now=local_now, start=start, end=end):
        return RuntimeMode(name="OPEN", market_open=True, reason="regular_session", local_dt=local_now)
    return RuntimeMode(name="CLOSED", market_open=False, reason="outside_regular_session", local_dt=local_now)


def _parse_hhmm(value: str) -> time:
    hour_str, minute_str = (value or "09:30").split(":", 1)
    return time(hour=int(hour_str), minute=int(minute_str))
