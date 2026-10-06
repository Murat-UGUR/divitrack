"""Port'lar (arayüzler). Altyapı katmanı bunları uygular; servisler yalnızca bunlara bağımlıdır."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol

from divitrack.core.entities import ProviderQuote


class PriceProvider(Protocol):
    """Piyasa verisi sağlayıcısı (yfinance, Alpha Vantage, sahte test sağlayıcısı...)."""

    name: str

    def fetch_quotes(self, symbols: Sequence[str]) -> dict[str, ProviderQuote]:
        """Son fiyat + önceki kapanış. Bulunamayan semboller sözlükte yer almaz."""
        ...

    def fetch_dividends(self, symbol: str) -> list[tuple[date, Decimal]]:
        """Hisse başı brüt temettü geçmişi (tarih, tutar), eskiden yeniye."""
        ...

    def fetch_history(self, symbols: Sequence[str], start: date) -> dict[str, list[tuple[date, Decimal]]]:
        """Günlük kapanış fiyatları, eskiden yeniye."""
        ...
