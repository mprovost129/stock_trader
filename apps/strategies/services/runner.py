"""Strategy runner.

Milestone 1:
- Execute strategies against stored PriceBars (read-only market data)
- Persist NEW signals (idempotent per instrument/strategy/timeframe/bar timestamp)
- Create a TradePlan immediately (stop-first sizing), when possible.

Milestone 3.5:
- Add optional scan diagnostics so operators can see *why* no signal fired.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from apps.marketdata.models import Instrument, PriceBar
from apps.signals.models import Signal
from apps.signals.services.tradeplan import ensure_trade_plan
from apps.strategies.models import StrategyRunConfig
from apps.strategies.registry import get as get_strategy, get_diagnostics


@dataclass(frozen=True)
class InstrumentScanResult:
    symbol: str
    created: bool
    reason: str


@dataclass(frozen=True)
class RunConfigResult:
    created_count: int
    scanned_count: int
    data_ready_count: int
    skipped_no_data_count: int
    results: list[InstrumentScanResult]
    summary_lines: list[str]


def run_config(
    config: StrategyRunConfig,
    *,
    instruments: list[Instrument] | None = None,
    limit: int = 300,
    user=None,
    collect_diagnostics: bool = False,
) -> int | RunConfigResult:
    """Run a single StrategyRunConfig over instruments and persist any new signals."""

    if not config.is_active or not config.strategy.is_enabled:
        result = RunConfigResult(created_count=0, scanned_count=0, data_ready_count=0, skipped_no_data_count=0, results=[], summary_lines=[])
        return result if collect_diagnostics else 0

    if instruments is None:
        instruments = list(Instrument.objects.filter(is_active=True))

    strategy_fn = get_strategy(config.strategy.slug)
    diagnostics_fn = get_diagnostics(config.strategy.slug)
    created = 0
    scanned = 0
    results: list[InstrumentScanResult] = []
    summary_lines: list[str] = []

    ready_ids = set(
        PriceBar.objects.filter(instrument__in=instruments, timeframe=config.timeframe)
        .values_list("instrument_id", flat=True)
        .distinct()
    )
    data_ready = [inst for inst in instruments if inst.id in ready_ids]
    skipped_no_data_count = len(instruments) - len(data_ready)
    if collect_diagnostics and skipped_no_data_count:
        summary_lines.append(
            f"suppressed {skipped_no_data_count} watchlist symbol(s) with no {config.timeframe} bars; scanning {len(data_ready)} data-ready symbol(s)"
        )

    for inst in data_ready:
        bars_qs = (
            PriceBar.objects.filter(instrument=inst, timeframe=config.timeframe)
            .order_by("-ts")
            .only("ts", "open", "high", "low", "close", "volume")[:limit]
        )
        bars = list(reversed(list(bars_qs)))
        if not bars:
            if collect_diagnostics:
                results.append(InstrumentScanResult(symbol=inst.symbol, created=False, reason=f"no {config.timeframe} bars available"))
            continue

        scanned += 1
        closes: list[Decimal] = [b.close for b in bars]
        highs: list[Decimal] = [b.high for b in bars]
        lows: list[Decimal] = [b.low for b in bars]
        volumes: list[Decimal] = [b.volume for b in bars]
        params = config.params or {}

        strategy_kwargs = {
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "volumes": volumes,
        }

        sig = strategy_fn(**strategy_kwargs, **params)
        if sig is None:
            if collect_diagnostics:
                reason = diagnostics_fn(**strategy_kwargs, **params) if diagnostics_fn else "strategy returned no signal"
                results.append(InstrumentScanResult(symbol=inst.symbol, created=False, reason=reason))
            continue

        signal_ts = bars[-1].ts

        exists = Signal.objects.filter(
            instrument=inst,
            strategy=config.strategy,
            timeframe=config.timeframe,
            direction=sig.direction,
            signal_kind=sig.signal_kind,
            signal_label=sig.signal_label,
            status=Signal.Status.NEW,
            generated_at=signal_ts,
        ).exists()
        if exists:
            if collect_diagnostics:
                results.append(InstrumentScanResult(symbol=inst.symbol, created=False, reason=f"duplicate NEW signal already exists for {signal_ts.isoformat()} {sig.signal_label or sig.signal_kind} {sig.direction}"))
            continue

        signal = Signal.objects.create(
            created_by=user,
            instrument=inst,
            strategy=config.strategy,
            timeframe=config.timeframe,
            direction=sig.direction,
            signal_kind=sig.signal_kind,
            signal_label=sig.signal_label,
            score=sig.score,
            score_components=sig.score_components,
            rationale=sig.rationale,
            generated_at=signal_ts,
        )

        ensure_trade_plan(signal, user=user)

        created += 1
        if collect_diagnostics:
            results.append(InstrumentScanResult(symbol=inst.symbol, created=True, reason=sig.rationale))

    run_result = RunConfigResult(created_count=created, scanned_count=scanned, data_ready_count=len(data_ready), skipped_no_data_count=skipped_no_data_count, results=results, summary_lines=summary_lines)
    return run_result if collect_diagnostics else created
