"""Strategy registry.

We keep implementations decoupled from models so we can run/backtest
without importing the Django ORM from the strategy code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class StrategySignal:
    direction: str  # LONG / SHORT / FLAT
    score: float  # 0-100 opportunity score
    rationale: str
    signal_kind: str = "EVENT"
    signal_label: str = ""
    score_components: dict[str, float] = field(default_factory=dict)


StrategyFn = Callable[..., StrategySignal | None]
StrategyExplainFn = Callable[..., str]

_REGISTRY: dict[str, StrategyFn] = {}
_DIAGNOSTICS: dict[str, StrategyExplainFn] = {}


def register(slug: str) -> Callable[[StrategyFn], StrategyFn]:
    def decorator(fn: StrategyFn) -> StrategyFn:
        _REGISTRY[slug] = fn
        return fn

    return decorator


def register_diagnostics(slug: str) -> Callable[[StrategyExplainFn], StrategyExplainFn]:
    def decorator(fn: StrategyExplainFn) -> StrategyExplainFn:
        _DIAGNOSTICS[slug] = fn
        return fn

    return decorator


def get_diagnostics(slug: str) -> StrategyExplainFn | None:
    return _DIAGNOSTICS.get(slug)


def get(slug: str) -> StrategyFn:
    try:
        return _REGISTRY[slug]
    except KeyError as exc:
        raise KeyError(f"Strategy '{slug}' is not registered") from exc


def all_slugs() -> list[str]:
    return sorted(_REGISTRY.keys())
