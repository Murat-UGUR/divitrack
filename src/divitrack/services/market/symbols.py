"""Uygulama sembolleri ↔ sağlayıcı (Yahoo) sembolleri eşlemesi."""

from __future__ import annotations

from decimal import Decimal

from divitrack.core.entities import Asset
from divitrack.core.enums import AssetClass, Currency

TROY_OUNCE_GRAMS = Decimal("31.1034768")

GOLD_FUTURES = "GC=F"
SILVER_FUTURES = "SI=F"

# Gram cinsinden TRY fiyatı ons(USD) × USDTRY ÷ 31.1035 ile türetilen semboller
DERIVED_GRAM_METALS: dict[str, str] = {"XAU-GRAM": GOLD_FUTURES, "XAG-GRAM": SILVER_FUTURES}
# Ons (USD) kısayolları
OUNCE_ALIASES: dict[str, str] = {"XAU-OZ": GOLD_FUTURES, "XAG-OZ": SILVER_FUTURES}

METAL_PRESETS: dict[str, tuple[str, Currency]] = {
    "XAU-GRAM": ("Gram Altın", Currency.TRY),
    "XAG-GRAM": ("Gram Gümüş", Currency.TRY),
    "XAU-OZ": ("Ons Altın", Currency.USD),
    "XAG-OZ": ("Ons Gümüş", Currency.USD),
}


def fx_symbol(base: Currency, quote: Currency) -> str:
    """1 `base` kaç `quote` eder → Yahoo sembolü (ör. USD,TRY → USDTRY=X)."""
    return f"{base.value}{quote.value}=X"


def provider_symbol(asset: Asset) -> str | None:
    """Varlığın sağlayıcıdaki sembolü. Fiyatı türetilen/manuel varlıklar için None."""
    if asset.asset_class is AssetClass.CASH or asset.manual_price is not None:
        return None
    sym = asset.symbol
    if asset.asset_class is AssetClass.BIST_STOCK:
        return sym if sym.endswith(".IS") else f"{sym}.IS"
    if asset.asset_class is AssetClass.PRECIOUS_METAL:
        if sym in DERIVED_GRAM_METALS:
            return None
        return OUNCE_ALIASES.get(sym, sym)
    return sym


def required_symbols(asset: Asset) -> list[str]:
    """Bir varlığın fiyatını hesaplamak için gereken tüm sağlayıcı sembolleri."""
    if asset.manual_price is not None or asset.asset_class is AssetClass.CASH:
        return []
    if asset.asset_class is AssetClass.PRECIOUS_METAL and asset.symbol in DERIVED_GRAM_METALS:
        return [DERIVED_GRAM_METALS[asset.symbol], fx_symbol(Currency.USD, Currency.TRY)]
    sym = provider_symbol(asset)
    return [sym] if sym else []


def ounce_to_gram_try(ounce_usd: Decimal, usdtry: Decimal) -> Decimal:
    return ounce_usd * usdtry / TROY_OUNCE_GRAMS
