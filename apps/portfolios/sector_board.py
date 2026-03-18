from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import OuterRef, Subquery

from apps.marketdata.models import PriceBar
from apps.signals.models import Signal

from .models import HeldPosition, InstrumentSelection
from .watchlists import review_status_for_selection


def build_watchlist_sector_board(*, user, watchlist, selections=None):
    if not watchlist:
        return {"rows": [], "totals": {"sectors": 0, "leaders": 0, "laggards": 0, "unassigned": 0}}

    if selections is None:
        latest_close_subquery = Subquery(
            PriceBar.objects.filter(
                instrument_id=OuterRef("instrument_id"),
                timeframe=PriceBar.Timeframe.D1,
            )
            .order_by("-ts")
            .values("close")[:1]
        )
        selections = list(
            InstrumentSelection.objects.select_related("instrument")
            .filter(watchlist=watchlist, is_active=True, instrument__is_active=True)
            .annotate(last_close=latest_close_subquery)
        )

    instrument_ids = [item.instrument_id for item in selections]
    open_holding_ids = set(HeldPosition.objects.filter(user=user, status=HeldPosition.Status.OPEN).values_list("instrument_id", flat=True))

    previous_close_map = {}
    if instrument_ids:
        prev_bars = (
            PriceBar.objects.filter(instrument_id__in=instrument_ids, timeframe=PriceBar.Timeframe.D1)
            .order_by("instrument_id", "-ts")
            .values("instrument_id", "close")
        )
        buckets = defaultdict(list)
        for row in prev_bars:
            bucket = buckets[row["instrument_id"]]
            if len(bucket) < 2:
                bucket.append(Decimal(str(row["close"])))
        for instrument_id, values in buckets.items():
            previous_close_map[instrument_id] = values[1] if len(values) > 1 else values[0]

    recent_signal_map = {}
    if instrument_ids:
        recent_signals = (
            Signal.objects.filter(instrument_id__in=instrument_ids)
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

    groups = defaultdict(lambda: {
        "sector": "",
        "symbols": 0,
        "held": 0,
        "longs": 0,
        "shorts": 0,
        "scored": 0,
        "avg_score_seed": [],
        "avg_move_seed": [],
        "leaders": [],
        "needs_review": 0,
    })

    for item in selections:
        sector = (item.sector or "").strip() or "Unassigned"
        signal = recent_signal_map.get(item.instrument_id)
        score_value = None
        if signal and signal.score is not None:
            try:
                score_value = float(signal.score)
            except (TypeError, ValueError):
                score_value = None
            if score_value is not None and score_value <= 1:
                score_value *= 100
        daily_move_pct = None
        current_close = getattr(item, "last_close", None)
        previous_close = previous_close_map.get(item.instrument_id)
        if current_close is not None and previous_close not in (None, Decimal("0")):
            try:
                daily_move_pct = float(((Decimal(str(current_close)) - previous_close) / previous_close) * Decimal("100"))
            except Exception:
                daily_move_pct = None

        row = groups[sector]
        row["sector"] = sector
        row["symbols"] += 1
        if item.instrument_id in open_holding_ids:
            row["held"] += 1
        review_status = getattr(item, "review_status", None) or review_status_for_selection(item)
        if review_status in {"STALE", "NEVER"}:
            row["needs_review"] += 1
        if signal:
            if signal.direction == Signal.Direction.LONG:
                row["longs"] += 1
            elif signal.direction == Signal.Direction.SHORT:
                row["shorts"] += 1
        if score_value is not None:
            row["scored"] += 1
            row["avg_score_seed"].append(score_value)
        if daily_move_pct is not None:
            row["avg_move_seed"].append(daily_move_pct)
        leader_strength = score_value if score_value is not None else -999
        row["leaders"].append({
            "symbol": item.instrument.symbol,
            "score": round(score_value, 1) if score_value is not None else None,
            "move_pct": round(daily_move_pct, 2) if daily_move_pct is not None else None,
            "strength": leader_strength,
        })

    rows = []
    for sector, item in groups.items():
        avg_score = round(sum(item["avg_score_seed"]) / len(item["avg_score_seed"]), 1) if item["avg_score_seed"] else None
        avg_move = round(sum(item["avg_move_seed"]) / len(item["avg_move_seed"]), 2) if item["avg_move_seed"] else None
        signal_balance = item["longs"] - item["shorts"]
        if signal_balance >= 2 or (signal_balance > 0 and (avg_score or 0) >= 70):
            posture = "leading"
        elif signal_balance <= -2 or (signal_balance < 0 and (avg_score or 0) < 50):
            posture = "weakening"
        else:
            posture = "mixed"
        leaders = sorted(item["leaders"], key=lambda leader: (-(leader["score"] if leader["score"] is not None else -1), -(leader["move_pct"] if leader["move_pct"] is not None else -999), leader["symbol"]))[:3]
        rows.append({
            "sector": sector,
            "symbols": item["symbols"],
            "held": item["held"],
            "longs": item["longs"],
            "shorts": item["shorts"],
            "needs_review": item["needs_review"],
            "avg_score": avg_score,
            "avg_move_pct": avg_move,
            "posture": posture,
            "leaders": leaders,
        })

    rows.sort(key=lambda row: (0 if row["posture"] == "leading" else 1 if row["posture"] == "mixed" else 2, -(row["avg_score"] or -1), -(row["avg_move_pct"] or -999), row["sector"]))
    return {
        "rows": rows,
        "totals": {
            "sectors": len(rows),
            "leaders": sum(1 for row in rows if row["posture"] == "leading"),
            "laggards": sum(1 for row in rows if row["posture"] == "weakening"),
            "unassigned": next((row["symbols"] for row in rows if row["sector"] == "Unassigned"), 0),
        },
    }
