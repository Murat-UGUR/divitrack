"""Portföy servisi: varlık/işlem yönetimi ve değerleme (özet, dağılım, K/Z)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from divitrack.core.calculations import compute_position
from divitrack.core.entities import Asset, PositionState, PriceQuote, Transaction
from divitrack.core.enums import AssetClass, Currency
from divitrack.core.exceptions import ValidationError
from divitrack.core.money import ZERO, pct
from divitrack.repositories.sqlite import AssetRepository, TransactionRepository
from divitrack.services.market.market_data_service import MarketDataService
from divitrack.services.market.symbols import DERIVED_GRAM_METALS, METAL_PRESETS


@dataclass(slots=True)
class PositionView:
    asset: Asset
    state: PositionState
    quote: PriceQuote | None
    fx: Decimal | None  # varlık para birimi → raporlama para birimi

    @property
    def priced(self) -> bool:
        return self.quote is not None and self.fx is not None

    @property
    def market_value(self) -> Decimal | None:
        return None if self.quote is None else self.state.quantity * self.quote.price

    @property
    def unrealized_pnl(self) -> Decimal | None:
        mv = self.market_value
        return None if mv is None else mv - self.state.cost_basis

    @property
    def unrealized_pct(self) -> Decimal | None:
        u = self.unrealized_pnl
        return None if u is None else pct(u, self.state.cost_basis)

    def _base(self, value: Decimal | None) -> Decimal | None:
        return None if value is None or self.fx is None else value * self.fx

    @property
    def market_value_base(self) -> Decimal | None:
        return self._base(self.market_value)

    @property
    def cost_basis_base(self) -> Decimal | None:
        return self._base(self.state.cost_basis)

    @property
    def unrealized_pnl_base(self) -> Decimal | None:
        return self._base(self.unrealized_pnl)

    @property
    def realized_pnl_base(self) -> Decimal | None:
        return self._base(self.state.realized_pnl)

    @property
    def day_change_base(self) -> Decimal | None:
        if self.quote is None:
            return None
        return self._base(self.state.quantity * self.quote.change)


@dataclass(slots=True)
class PortfolioSummary:
    base_currency: Currency
    positions: list[PositionView]
    total_value: Decimal = ZERO
    total_cost: Decimal = ZERO
    unrealized_pnl: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    day_change: Decimal = ZERO
    allocation_by_class: dict[AssetClass, Decimal] = field(default_factory=dict)
    unpriced: list[Asset] = field(default_factory=list)
    stale: bool = False

    @property
    def unrealized_pct(self) -> Decimal | None:
        return pct(self.unrealized_pnl, self.total_cost)

    @property
    def day_change_pct(self) -> Decimal | None:
        return pct(self.day_change, self.total_value - self.day_change)

    @property
    def open_positions(self) -> list[PositionView]:
        return [p for p in self.positions if p.state.is_open]


class PortfolioService:
    def __init__(self, assets: AssetRepository, transactions: TransactionRepository, market: MarketDataService) -> None:
        self.assets = assets
        self.transactions = transactions
        self.market = market

    # ------------------------------------------------------------------ varlıklar
    def add_asset(self, asset: Asset) -> Asset:
        self._normalize(asset)
        if (
            asset.asset_class is AssetClass.FUND
            and not asset.name
            and self.assets.find(asset.symbol, asset.asset_class) is None
        ):
            asset.name = (self.market.describe(asset) or "")[:120]
        return self.assets.add(asset)

    def update_asset(self, asset: Asset) -> Asset:
        self._normalize(asset)
        return self.assets.update(asset)

    def delete_asset(self, asset_id: int) -> None:
        self.assets.delete(asset_id)

    def get_or_create_asset(self, symbol: str, asset_class: AssetClass, currency: Currency, name: str = "") -> Asset:
        existing = self.assets.find(symbol, asset_class)
        if existing is not None:
            return existing
        return self.add_asset(Asset(symbol=symbol, asset_class=asset_class, currency=currency, name=name))

    @staticmethod
    def _normalize(asset: Asset) -> None:
        if asset.asset_class is AssetClass.BIST_STOCK:
            asset.symbol = asset.symbol.removesuffix(".IS")
            if asset.currency is not Currency.TRY:
                raise ValidationError("BIST hisseleri TRY cinsinden olmalı")
        if asset.asset_class is AssetClass.FUND:
            asset.currency = Currency.TRY  # TEFAS fonları TL fiyatlanır
        if asset.asset_class is AssetClass.PRECIOUS_METAL and asset.symbol in METAL_PRESETS:
            preset_name, preset_ccy = METAL_PRESETS[asset.symbol]
            asset.name = asset.name or preset_name
            if asset.symbol in DERIVED_GRAM_METALS:
                asset.currency = preset_ccy

    # ------------------------------------------------------------------ işlemler
    def add_transaction(self, tx: Transaction) -> Transaction:
        self.assets.get(tx.asset_id)  # varlık var mı
        existing = self.transactions.list(tx.asset_id)
        compute_position([*existing, tx])  # açığa satışı engeller (InsufficientQuantityError)
        return self.transactions.add(tx)

    def delete_transaction(self, tx_id: int) -> None:
        txs = self.transactions.list()
        target = next((t for t in txs if t.id == tx_id), None)
        if target is None:
            return
        remaining = [t for t in txs if t.asset_id == target.asset_id and t.id != tx_id]
        compute_position(remaining)  # silince geçmişte negatif adet oluşuyorsa engelle
        self.transactions.delete(tx_id)

    # ------------------------------------------------------------------ değerleme
    def positions(self, *, as_of: date | None = None) -> list[tuple[Asset, PositionState]]:
        grouped = self.transactions.by_asset()
        return [(a, compute_position(grouped.get(a.id or -1, []), as_of=as_of)) for a in self.assets.list()]

    def summary(self, base: Currency, *, force_refresh: bool = False) -> PortfolioSummary:
        pairs = self.positions()
        open_assets = [a for a, s in pairs if s.is_open]
        quotes = self.market.quotes_for(open_assets, force=force_refresh)
        currencies = {q.currency for q in quotes.values()} | {a.currency for a, _ in pairs}
        rates = self.market.fx_rates(currencies, base, force=force_refresh)

        summary = PortfolioSummary(base_currency=base, positions=[])
        by_class: dict[AssetClass, Decimal] = defaultdict(lambda: ZERO)
        for asset, state in pairs:
            quote = quotes.get(asset.id or -1)
            fx = rates.get(quote.currency if quote else asset.currency)
            view = PositionView(asset, state, quote, fx)
            summary.positions.append(view)
            if view.realized_pnl_base is not None:
                summary.realized_pnl += view.realized_pnl_base
            if not state.is_open:
                continue
            if not view.priced:
                summary.unpriced.append(asset)
                continue
            assert view.market_value_base is not None and view.cost_basis_base is not None
            summary.total_value += view.market_value_base
            summary.total_cost += view.cost_basis_base
            summary.unrealized_pnl += view.unrealized_pnl_base or ZERO
            summary.day_change += view.day_change_base or ZERO
            by_class[asset.asset_class] += view.market_value_base
            summary.stale |= bool(quote and quote.stale)
        summary.allocation_by_class = dict(by_class)
        return summary
