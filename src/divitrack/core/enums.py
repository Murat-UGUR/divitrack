"""Domain sabitleri."""

from __future__ import annotations

from enum import StrEnum


class AssetClass(StrEnum):
    BIST_STOCK = "BIST_STOCK"
    US_STOCK = "US_STOCK"
    PRECIOUS_METAL = "PRECIOUS_METAL"
    CASH = "CASH"

    @property
    def label(self) -> str:
        return _ASSET_CLASS_LABELS[self]


_ASSET_CLASS_LABELS = {
    AssetClass.BIST_STOCK: "BIST Hisse",
    AssetClass.US_STOCK: "ABD Hisse/ETF",
    AssetClass.PRECIOUS_METAL: "Kıymetli Maden",
    AssetClass.CASH: "Nakit/BES",
}


class Currency(StrEnum):
    TRY = "TRY"
    USD = "USD"
    EUR = "EUR"

    @property
    def symbol(self) -> str:
        return {"TRY": "₺", "USD": "$", "EUR": "€"}[self.value]


class TxType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def label(self) -> str:
        return "Alış" if self is TxType.BUY else "Satış"


class DividendStatus(StrEnum):
    EXPECTED = "EXPECTED"
    PAID = "PAID"

    @property
    def label(self) -> str:
        return "Beklenen" if self is DividendStatus.EXPECTED else "Ödendi"
