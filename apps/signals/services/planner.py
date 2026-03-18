"""Trade plan computation.

Milestone 0: simple stop-first sizing. Targets are placeholders.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def suggested_qty(*, account_equity: Decimal, risk_pct: Decimal, entry: Decimal, stop: Decimal) -> int | None:
    try:
        risk_dollars = account_equity * risk_pct
        per_unit_risk = abs(entry - stop)
        if per_unit_risk <= 0:
            return None
        qty = int(risk_dollars / per_unit_risk)
        return max(qty, 0)
    except (InvalidOperation, ZeroDivisionError):
        return None
