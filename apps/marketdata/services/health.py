from __future__ import annotations

from dataclasses import dataclass

from apps.marketdata.models import Instrument
from apps.marketdata.services.ingestion import ingest_from_provider


@dataclass(frozen=True)
class ProviderHealthResult:
    provider: str
    ok: bool
    message: str


def provider_healthcheck(*, provider: str) -> ProviderHealthResult:
    provider_name = (provider or "").strip().lower()
    if provider_name == "yahoo":
        return _probe_stock_provider("yahoo")
    if provider_name == "polygon":
        return _probe_stock_provider("polygon")
    if provider_name == "coinbase":
        return _probe_crypto_provider("coinbase")
    raise ValueError(f"Unsupported provider healthcheck: {provider}")


def _probe_stock_provider(provider_name: str) -> ProviderHealthResult:
    symbol = "AAPL"
    if not Instrument.objects.filter(symbol=symbol).exists():
        return ProviderHealthResult(provider_name, False, f"instrument_missing:{symbol}")
    try:
        res = ingest_from_provider(symbol=symbol, timeframe="1d", limit=5, provider_name=provider_name)
        return ProviderHealthResult(provider_name, True, f"ok created={res.created} updated={res.updated}")
    except Exception as exc:  # noqa: BLE001
        return ProviderHealthResult(provider_name, False, str(exc))


def _probe_crypto_provider(provider_name: str) -> ProviderHealthResult:
    symbol = "BTC"
    if not Instrument.objects.filter(symbol=symbol).exists():
        return ProviderHealthResult(provider_name, False, f"instrument_missing:{symbol}")
    try:
        res = ingest_from_provider(symbol=symbol, timeframe="1d", limit=5, provider_name=provider_name)
        return ProviderHealthResult(provider_name, True, f"ok created={res.created} updated={res.updated}")
    except Exception as exc:  # noqa: BLE001
        return ProviderHealthResult(provider_name, False, str(exc))
