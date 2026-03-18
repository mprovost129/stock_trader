from __future__ import annotations

from functools import lru_cache

import requests

from apps.marketdata.services.ingestion_state import get_unsupported_crypto_reason, mark_unsupported_crypto_symbol
from apps.marketdata.services.providers.binance import BinanceProvider
from apps.marketdata.services.providers.coinbase import CoinbaseAdvancedProvider
from apps.marketdata.services.providers.kraken import KrakenProvider

INVALID_CRYPTO_SYMBOLS = {"USD", "USDT", "USDC", "DAI", "USDE", "FDUSD", "TUSD"}
MIN_CRYPTO_SYMBOL_LENGTH = 3


class UnsupportedCryptoSymbolError(RuntimeError):
    pass


def valid_crypto_symbol(symbol: str) -> bool:
    sym = (symbol or "").strip().upper()
    if not sym or len(sym) < MIN_CRYPTO_SYMBOL_LENGTH:
        return False
    if sym in INVALID_CRYPTO_SYMBOLS:
        return False
    if get_unsupported_crypto_reason(sym):
        return False
    return True


@lru_cache(maxsize=1)
def _coinbase_products() -> set[str]:
    r = requests.get(f"{CoinbaseAdvancedProvider().base_url}/products", timeout=30)
    r.raise_for_status()
    return {str(row.get("id") or "").upper() for row in (r.json() or []) if row.get("id")}


@lru_cache(maxsize=1)
def _kraken_pairs() -> set[str]:
    r = requests.get(f"{KrakenProvider().base_url}/0/public/AssetPairs", timeout=30)
    r.raise_for_status()
    payload = r.json() or {}
    result = payload.get("result") or {}
    pairs = set()
    for key, value in result.items():
        pairs.add(str(key).upper())
        alt = str(value.get("altname") or "").upper()
        ws = str(value.get("wsname") or "").upper().replace("/", "")
        if alt:
            pairs.add(alt)
        if ws:
            pairs.add(ws)
    return pairs


@lru_cache(maxsize=1)
def _binance_pairs() -> set[str]:
    r = requests.get(f"{BinanceProvider().base_url}/api/v3/exchangeInfo", timeout=30)
    r.raise_for_status()
    rows = (r.json() or {}).get("symbols") or []
    return {str(row.get("symbol") or "").upper() for row in rows if row.get("symbol")}


@lru_cache(maxsize=512)
def resolve_crypto_provider(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not valid_crypto_symbol(sym):
        raise UnsupportedCryptoSymbolError(f"invalid_or_unsupported_crypto_symbol:{sym}")
    if f"{sym}-USD" in _coinbase_products():
        return "coinbase"
    if ({"XBTUSD"} if sym == "BTC" else {f"{sym}USD"}) & _kraken_pairs():
        return "kraken"
    if f"{sym}USDT" in _binance_pairs() or f"{sym}USD" in _binance_pairs() or f"{sym}BUSD" in _binance_pairs():
        return "binance"
    mark_unsupported_crypto_symbol(sym, f"unsupported_crypto_pair:{sym}")
    raise UnsupportedCryptoSymbolError(f"unsupported_crypto_pair:{sym}")
