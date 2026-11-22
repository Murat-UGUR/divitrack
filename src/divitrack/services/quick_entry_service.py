"""Metinle hızlı işlem girişi: "5 gr altın 5750" → işlem defteri kaydı (önce önizleme, sonra onay)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from divitrack.core.entities import Asset, Transaction
from divitrack.core.exceptions import ValidationError
from divitrack.core.quick_entry import ParsedEntry, parse_entry
from divitrack.services.portfolio_service import PortfolioService


@dataclass(frozen=True, slots=True)
class PreparedEntry:
    parsed: ParsedEntry
    asset: Asset
    transaction: Transaction
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        t = self.transaction
        verb = "Alış" if t.tx_type == "BUY" else "Satış"
        ccy = self.asset.currency.symbol
        return (
            f"{verb}: {t.quantity.normalize():f} {self.asset.symbol} × {t.price.normalize():f} {ccy} "
            f"= {(t.quantity * t.price).quantize(t.price.__class__('0.01')):f} {ccy} · {t.trade_date:%d.%m.%Y}"
        )


class QuickEntryService:
    def __init__(self, portfolio: PortfolioService, today: Callable[[], date] = date.today) -> None:
        self.portfolio = portfolio
        self.today = today

    def prepare(self, text: str) -> PreparedEntry:
        p = parse_entry(text, self.today())
        matches = [a for a in self.portfolio.assets.list() if a.symbol == p.symbol]
        if not matches:
            raise ValidationError(
                f"{p.symbol} kayıtlı değil. Önce Varlıklar sayfasından ekle (altın için XAU-GRAM, gümüş için XAG-GRAM)."
            )
        asset = matches[0]
        warnings: list[str] = []
        if p.currency is not None and p.currency is not asset.currency:
            warnings.append(
                f"Yazdığın para birimi ({p.currency.value}) varlığın para biriminden ({asset.currency.value}) farklı; "
                f"fiyat {asset.currency.value} kabul edildi."
            )
        assert asset.id is not None
        tx = Transaction(asset.id, p.tx_type, p.quantity, p.price, p.trade_date, note="hızlı giriş")
        return PreparedEntry(p, asset, tx, warnings)

    def save(self, entry: PreparedEntry) -> Transaction:
        return self.portfolio.add_transaction(entry.transaction)
