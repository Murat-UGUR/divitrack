"""Domain sabitleri."""

from __future__ import annotations

from enum import StrEnum


class AssetClass(StrEnum):
    BIST_STOCK = "BIST_STOCK"
    US_STOCK = "US_STOCK"
    PRECIOUS_METAL = "PRECIOUS_METAL"
    FUND = "FUND"
    CASH = "CASH"

    @property
    def label(self) -> str:
        return _ASSET_CLASS_LABELS[self]


_ASSET_CLASS_LABELS = {
    AssetClass.BIST_STOCK: "BIST Hisse",
    AssetClass.US_STOCK: "ABD Hisse/ETF",
    AssetClass.PRECIOUS_METAL: "Kıymetli Maden",
    AssetClass.FUND: "Yatırım Fonu",
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
    """İşlem defteri kayıt tipleri.

    BONUS: bedelsiz sermaye artırımı veya hisse bölünmesi — adet artar, maliyet değişmez.
    RIGHTS: bedelli sermaye artırımı (rüçhan kullanımı) — nominal fiyattan alış gibi işler.
    REINVEST: temettünün aynı varlığa yeniden yatırılması — yeni para girişi sayılmaz.
    """

    BUY = "BUY"
    SELL = "SELL"
    BONUS = "BONUS"
    RIGHTS = "RIGHTS"
    REINVEST = "REINVEST"

    @property
    def label(self) -> str:
        return _TX_LABELS[self]

    @property
    def is_new_money(self) -> bool:
        """Cepten çıkan yeni para mı? (enflasyon ve yatırılan tutar hesapları için)"""
        return self in (TxType.BUY, TxType.RIGHTS)


_TX_LABELS = {
    TxType.BUY: "Alış",
    TxType.SELL: "Satış",
    TxType.BONUS: "Bedelsiz / bölünme",
    TxType.RIGHTS: "Bedelli (rüçhan)",
    TxType.REINVEST: "Temettüden alış",
}


class Purpose(StrEnum):
    """Varlığın portföydeki görevi. Dağılım hedefleri amaca göre tanımlanır."""

    DEFENSIVE = "DEFENSIVE"
    GROWTH = "GROWTH"
    DIVIDEND = "DIVIDEND"
    EXPERIMENT = "EXPERIMENT"
    UNASSIGNED = "UNASSIGNED"

    @property
    def label(self) -> str:
        return _PURPOSE_LABELS[self]

    @property
    def hint(self) -> str:
        return _PURPOSE_HINTS[self]

    @classmethod
    def targeted(cls) -> list[Purpose]:
        """Hedef oranı tanımlanabilen amaçlar."""
        return [p for p in cls if p is not cls.UNASSIGNED]


_PURPOSE_LABELS = {
    Purpose.DEFENSIVE: "Koruma",
    Purpose.GROWTH: "Büyüme",
    Purpose.DIVIDEND: "Temettü geliri",
    Purpose.EXPERIMENT: "Deneme",
    Purpose.UNASSIGNED: "Atanmadı",
}

_PURPOSE_HINTS = {
    Purpose.DEFENSIVE: "Gerektiğinde nakde dönebilen, değer koruyan varlıklar (altın, döviz, mevduat)",
    Purpose.GROWTH: "Uzun vadede değer artışı beklenen hisse/fonlar",
    Purpose.DIVIDEND: "Düzenli nakit akışı için tutulan temettü varlıkları",
    Purpose.EXPERIMENT: "Küçük tutarla denenen fikirler; deneme bütçesiyle sınırlı",
    Purpose.UNASSIGNED: "Henüz bir amaca bağlanmamış",
}


class FlowKind(StrEnum):
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"

    @property
    def label(self) -> str:
        return "Gelir" if self is FlowKind.INCOME else "Gider"


class DividendStatus(StrEnum):
    EXPECTED = "EXPECTED"
    PAID = "PAID"

    @property
    def label(self) -> str:
        return "Beklenen" if self is DividendStatus.EXPECTED else "Ödendi"
