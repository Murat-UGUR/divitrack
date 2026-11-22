"""Yurt dışı hisse/ETF satış kazancı ön izlemesi (Yİ-ÜFE endekslemeli, TL)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from divitrack.core.entities import Asset
from divitrack.core.enums import AssetClass, Currency, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.core.money import ZERO
from divitrack.core.tax import TaxableGain, realized_lots, taxable_gains
from divitrack.repositories.sqlite import PpiRepository, SettingsRepository
from divitrack.services.market.market_data_service import value_on
from divitrack.services.portfolio_service import PortfolioService

EXEMPTION_KEY = "tax.foreign_gain_exemption"


@dataclass(frozen=True, slots=True)
class AssetGain:
    asset: Asset
    gains: list[TaxableGain]

    @property
    def total(self) -> Decimal:
        return sum((g.gain_try for g in self.gains), ZERO)


@dataclass(slots=True)
class TaxPreview:
    year: int
    rows: list[AssetGain] = field(default_factory=list)
    exemption: Decimal | None = None
    missing_fx: list[str] = field(default_factory=list)
    ppi_missing: bool = False

    @property
    def net_gain(self) -> Decimal:
        return sum((r.total for r in self.rows), ZERO)

    @property
    def taxable(self) -> Decimal:
        """Zararlar aynı yıl içindeki kazançlardan düşülür; istisna (girildiyse) sonra uygulanır."""
        base = max(ZERO, self.net_gain)
        if self.exemption:
            base = max(ZERO, base - self.exemption)
        return base


class TaxService:
    def __init__(self, portfolio: PortfolioService, ppi: PpiRepository, settings: SettingsRepository) -> None:
        self.portfolio = portfolio
        self.ppi = ppi
        self.settings = settings

    def exemption(self) -> Decimal | None:
        raw = self.settings.get(EXEMPTION_KEY)
        return Decimal(raw) if raw else None

    def set_exemption(self, value: Decimal | None) -> None:
        if value is not None and value < 0:
            raise ValidationError("İstisna tutarı negatif olamaz")
        self.settings.set(EXEMPTION_KEY, "" if value is None else str(value))

    def years_with_sales(self) -> list[int]:
        foreign = {a.id for a in self.portfolio.assets.list() if a.asset_class is AssetClass.US_STOCK}
        return sorted(
            {
                t.trade_date.year
                for t in self.portfolio.transactions.list()
                if t.asset_id in foreign and t.tx_type is TxType.SELL
            },
            reverse=True,
        )

    def foreign_sales(self, year: int) -> TaxPreview:
        preview = TaxPreview(year, exemption=self.exemption())
        grouped = self.portfolio.transactions.by_asset()
        ppi = self.ppi.all()
        market = self.portfolio.market
        for asset in self.portfolio.assets.list():
            if asset.asset_class is not AssetClass.US_STOCK or asset.id not in grouped:
                continue
            lots = [lot for lot in realized_lots(grouped[asset.id]) if lot.sell_date.year == year]
            if not lots:
                continue
            fx_map: dict[date, Decimal] | None = None
            if asset.currency is not Currency.TRY:
                start = min(lot.buy_date for lot in lots) - timedelta(days=7)
                series = market.fx_history({asset.currency}, Currency.TRY, start).get(asset.currency, {})
                fx_map = {}
                for d in {lot.buy_date for lot in lots} | {lot.sell_date for lot in lots}:
                    rate = value_on(series, d)
                    if rate is not None:
                        fx_map[d] = rate
            gains = taxable_gains(lots, fx_map, ppi)
            if len(gains) < len(lots):
                preview.missing_fx.append(asset.symbol)
            preview.ppi_missing |= any(g.ppi_missing for g in gains)
            if gains:
                preview.rows.append(AssetGain(asset, gains))
        return preview
