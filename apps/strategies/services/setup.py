from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.marketdata.models import Instrument
from apps.portfolios.models import InstrumentSelection, UserRiskProfile, Watchlist
from apps.strategies.models import Strategy, StrategyRunConfig

DEFAULT_STARTER_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "BTC", "ETH"]
DEFAULT_STRATEGY_SLUG = "moving_average_crossover"
DEFAULT_STRATEGY_NAME = "Moving Average Crossover"
DEFAULT_TIMEFRAME = "1d"
DEFAULT_PARAMS = {"fast_ma": 5, "slow_ma": 10, "min_volume": 0, "signal_mode": "state"}
CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK", "LTC"}


@dataclass(frozen=True)
class DefaultSetupResult:
    username: str
    watchlist_name: str
    watchlist_created: bool
    selections_added: int
    instruments_created: int
    strategy_created: bool
    run_config_created: bool
    risk_profile_created: bool


def _infer_asset_class(symbol: str) -> str:
    return Instrument.AssetClass.CRYPTO if symbol.upper() in CRYPTO_SYMBOLS else Instrument.AssetClass.STOCK


def ensure_default_setup(
    *,
    username: str,
    watchlist_name: str = "Default",
    starter_symbols: list[str] | None = None,
    account_equity: str | Decimal | None = None,
    risk_pct: str | Decimal | None = None,
) -> DefaultSetupResult:
    User = get_user_model()
    user = User.objects.filter(username=username).first()
    if not user:
        raise ValueError(f"User not found: {username}")

    starter_symbols = starter_symbols or list(DEFAULT_STARTER_SYMBOLS)

    watchlist, watchlist_created = Watchlist.objects.get_or_create(user=user, name=watchlist_name)

    instruments_created = 0
    selections_added = 0
    for raw_symbol in starter_symbols:
        symbol = (raw_symbol or "").strip().upper()
        if not symbol:
            continue
        instrument, instrument_created = Instrument.objects.get_or_create(
            symbol=symbol,
            defaults={
                "name": symbol,
                "asset_class": _infer_asset_class(symbol),
                "is_active": True,
            },
        )
        if instrument_created:
            instruments_created += 1
        elif not instrument.is_active:
            instrument.is_active = True
            instrument.save(update_fields=["is_active", "updated_at"])

        _, selection_created = InstrumentSelection.objects.get_or_create(
            watchlist=watchlist,
            instrument=instrument,
            defaults={"is_active": True},
        )
        if selection_created:
            selections_added += 1

    strategy, strategy_created = Strategy.objects.get_or_create(
        slug=DEFAULT_STRATEGY_SLUG,
        defaults={
            "name": DEFAULT_STRATEGY_NAME,
            "description": "Starter moving-average crossover strategy used to prove the end-to-end pipeline.",
            "is_enabled": True,
        },
    )
    if not strategy.is_enabled:
        strategy.is_enabled = True
        strategy.save(update_fields=["is_enabled", "updated_at"])

    run_config, run_config_created = StrategyRunConfig.objects.get_or_create(
        strategy=strategy,
        timeframe=DEFAULT_TIMEFRAME,
        defaults={
            "params": DEFAULT_PARAMS,
            "is_active": True,
        },
    )
    changed_fields: list[str] = []
    if not run_config.is_active:
        run_config.is_active = True
        changed_fields.append("is_active")
    merged_params = dict(DEFAULT_PARAMS)
    merged_params.update(run_config.params or {})
    if run_config.params != merged_params:
        run_config.params = merged_params
        changed_fields.append("params")
    if changed_fields:
        changed_fields.append("updated_at")
        run_config.save(update_fields=changed_fields)

    risk_profile, risk_profile_created = UserRiskProfile.objects.get_or_create(user=user)
    risk_changed: list[str] = []
    if account_equity is not None:
        risk_profile.account_equity = account_equity
        risk_changed.append("account_equity")
    if risk_pct is not None:
        risk_profile.risk_per_trade_pct = risk_pct
        risk_changed.append("risk_per_trade_pct")
    if risk_changed:
        risk_changed.append("updated_at")
        risk_profile.save(update_fields=risk_changed)

    return DefaultSetupResult(
        username=user.username,
        watchlist_name=watchlist.name,
        watchlist_created=watchlist_created,
        selections_added=selections_added,
        instruments_created=instruments_created,
        strategy_created=strategy_created,
        run_config_created=run_config_created,
        risk_profile_created=risk_profile_created,
    )
