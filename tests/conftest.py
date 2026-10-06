"""Ortak test fixture'ları: bellek içi DB + ağ gerektirmeyen sahte fiyat sağlayıcısı."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from divitrack.bootstrap import Container, build_container
from divitrack.config import Settings
from divitrack.core.entities import ProviderQuote


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.prices: dict[str, tuple[str, str | None]] = {
            "THYAO.IS": ("300", "290"),
            "AAPL": ("200", "198"),
            "SCHD": ("27", "27"),
            "GC=F": ("2400", "2380"),
            "USDTRY=X": ("40", "40"),
            "EURTRY=X": ("45", "45"),
        }
        self.dividends: dict[str, list[tuple[date, Decimal]]] = {}
        self.history: dict[str, list[tuple[date, Decimal]]] = {}
        self.calls = 0
        self.fail = False

    def fetch_quotes(self, symbols: Sequence[str]) -> dict[str, ProviderQuote]:
        self.calls += 1
        if self.fail:
            raise ConnectionError("ağ yok")
        now = datetime.now()
        return {
            s: ProviderQuote(s, Decimal(p), Decimal(prev) if prev else None, now)
            for s, (p, prev) in self.prices.items()
            if s in symbols
        }

    def fetch_dividends(self, symbol: str) -> list[tuple[date, Decimal]]:
        return self.dividends.get(symbol, [])

    def fetch_history(self, symbols: Sequence[str], start: date) -> dict[str, list[tuple[date, Decimal]]]:
        return {s: [(d, v) for d, v in self.history.get(s, []) if d >= start] for s in symbols}


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def container(tmp_path: Path, provider: FakeProvider) -> Container:
    settings = Settings(db_path=tmp_path / "test.db", base_currency="TRY")
    return build_container(settings, provider=provider)
