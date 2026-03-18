"""Risk sizing utilities.

Milestone 0: stop-first sizing (same rule as the scaffold PDF).
"""

from __future__ import annotations

from decimal import Decimal

from apps.signals.services.planner import suggested_qty


def size_position(*, account_equity: Decimal, risk_pct: Decimal, entry: Decimal, stop: Decimal) -> int | None:
    return suggested_qty(account_equity=account_equity, risk_pct=risk_pct, entry=entry, stop=stop)
