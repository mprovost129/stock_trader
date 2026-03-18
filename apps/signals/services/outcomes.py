from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from apps.marketdata.models import PriceBar
from apps.signals.models import Signal, SignalOutcome


def _to_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def evaluate_signal_outcome(signal: Signal, *, lookahead_bars: int = 5) -> tuple[SignalOutcome, str]:
    outcome, _ = SignalOutcome.objects.get_or_create(signal=signal)
    outcome.lookahead_bars = lookahead_bars

    reference_price = None
    if getattr(signal, "trade_plan", None) and signal.trade_plan.entry_price:
        reference_price = signal.trade_plan.entry_price
    else:
        ref_bar = PriceBar.objects.filter(instrument=signal.instrument, timeframe=signal.timeframe, ts=signal.generated_at).order_by('-ts').first()
        if ref_bar:
            reference_price = ref_bar.close
    if reference_price is None:
        outcome.status = SignalOutcome.Status.INSUFFICIENT
        outcome.outcome_label = SignalOutcome.OutcomeLabel.FLAT
        outcome.evaluation_notes = 'Reference price unavailable for signal.'
        outcome.bars_observed = 0
        outcome.reference_price = None
        outcome.evaluated_at = timezone.now()
        outcome.save()
        return outcome, 'missing_reference_price'

    bars = list(
        PriceBar.objects.filter(
            instrument=signal.instrument,
            timeframe=signal.timeframe,
            ts__gt=signal.generated_at,
        ).order_by('ts')[:lookahead_bars]
    )
    outcome.reference_price = reference_price
    outcome.bars_observed = len(bars)

    if not bars:
        outcome.status = SignalOutcome.Status.PENDING
        outcome.outcome_label = SignalOutcome.OutcomeLabel.OPEN
        outcome.return_pct = None
        outcome.max_favorable_excursion_pct = None
        outcome.max_adverse_excursion_pct = None
        outcome.target_1_hit = False
        outcome.target_2_hit = False
        outcome.stop_hit = False
        outcome.evaluation_notes = 'Awaiting future bars.'
        outcome.evaluated_at = None
        outcome.save()
        return outcome, 'pending_future_bars'

    ref = float(reference_price)
    final_close = float(bars[-1].close)
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]

    if signal.direction == Signal.Direction.LONG:
        favorable = max(((h - ref) / ref) * 100.0 for h in highs)
        adverse = min(((l - ref) / ref) * 100.0 for l in lows)
        ret = ((final_close - ref) / ref) * 100.0
    elif signal.direction == Signal.Direction.SHORT:
        favorable = max(((ref - l) / ref) * 100.0 for l in lows)
        adverse = min(((ref - h) / ref) * 100.0 for h in highs)
        ret = ((ref - final_close) / ref) * 100.0
    else:
        favorable = 0.0
        adverse = 0.0
        ret = 0.0

    tp1 = getattr(getattr(signal, 'trade_plan', None), 'target_1', None)
    tp2 = getattr(getattr(signal, 'trade_plan', None), 'target_2', None)
    stop = getattr(getattr(signal, 'trade_plan', None), 'stop_price', None)

    target_1_hit = False
    target_2_hit = False
    stop_hit = False
    ambiguous = False

    for bar in bars:
        high = float(bar.high)
        low = float(bar.low)
        long_side = signal.direction == Signal.Direction.LONG
        if tp1 is not None:
            tp1f = float(tp1)
            target_1_hit = target_1_hit or (high >= tp1f if long_side else low <= tp1f)
        if tp2 is not None:
            tp2f = float(tp2)
            target_2_hit = target_2_hit or (high >= tp2f if long_side else low <= tp2f)
        if stop is not None:
            stopf = float(stop)
            stop_hit = stop_hit or (low <= stopf if long_side else high >= stopf)
        if stop is not None and tp1 is not None:
            if long_side and low <= float(stop) and high >= float(tp1):
                ambiguous = True
            if (not long_side) and high >= float(stop) and low <= float(tp1):
                ambiguous = True

    if signal.direction == Signal.Direction.FLAT:
        label = SignalOutcome.OutcomeLabel.FLAT
    elif ambiguous:
        label = SignalOutcome.OutcomeLabel.AMBIGUOUS
    elif target_2_hit or target_1_hit or ret > 0:
        label = SignalOutcome.OutcomeLabel.WIN if not stop_hit else SignalOutcome.OutcomeLabel.MIXED
    elif stop_hit or ret < 0:
        label = SignalOutcome.OutcomeLabel.LOSS
    else:
        label = SignalOutcome.OutcomeLabel.OPEN

    status = SignalOutcome.Status.EVALUATED if len(bars) >= lookahead_bars else SignalOutcome.Status.INSUFFICIENT
    note = f'Evaluated over {len(bars)} bar(s); label={label}.'
    if status == SignalOutcome.Status.INSUFFICIENT:
        note = f'Partial evaluation over {len(bars)}/{lookahead_bars} bar(s); label={label}.'

    outcome.status = status
    outcome.outcome_label = label
    outcome.return_pct = round(ret, 4)
    outcome.max_favorable_excursion_pct = round(favorable, 4)
    outcome.max_adverse_excursion_pct = round(adverse, 4)
    outcome.target_1_hit = target_1_hit
    outcome.target_2_hit = target_2_hit
    outcome.stop_hit = stop_hit
    outcome.evaluation_notes = note
    outcome.evaluated_at = timezone.now() if status == SignalOutcome.Status.EVALUATED else None
    outcome.save()
    return outcome, 'evaluated' if status == SignalOutcome.Status.EVALUATED else 'partial'
