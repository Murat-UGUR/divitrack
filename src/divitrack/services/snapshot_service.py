"""Portföy değer geçmişi: günlük anlık görüntü ve geçmişe dönük yeniden oluşturma."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from divitrack.core.calculations import compute_position
from divitrack.core.entities import Asset, AssetSnapshot, Snapshot
from divitrack.core.enums import Currency
from divitrack.core.money import ZERO
from divitrack.repositories.sqlite import AssetSnapshotRepository, SnapshotRepository
from divitrack.services.market.market_data_service import value_on
from divitrack.services.portfolio_service import PortfolioService, PortfolioSummary


@dataclass(frozen=True, slots=True)
class ChangeRow:
    """İki kayıt arasında bir varlığın değer farkı ve kaynağı (raporlama para biriminde)."""

    asset: Asset
    value_before: Decimal
    value_after: Decimal
    price_before: Decimal | None
    price_after: Decimal | None
    price_effect: Decimal  # eski adet × fiyat farkı
    flow_effect: Decimal  # yeni alım / satış / temettüden gelen adet değişimi
    cost_added: Decimal  # bu sürede eklenen (eksi: çıkan) maliyet

    @property
    def change(self) -> Decimal:
        return self.value_after - self.value_before

    @property
    def price_change_pct(self) -> Decimal | None:
        if not self.price_before or self.price_after is None:
            return None
        return (self.price_after / self.price_before - 1) * 100


@dataclass(frozen=True, slots=True)
class ChangeReport:
    since: date
    until: date
    rows: list[ChangeRow]

    @property
    def total_change(self) -> Decimal:
        return sum((r.change for r in self.rows), ZERO)

    @property
    def price_effect(self) -> Decimal:
        return sum((r.price_effect for r in self.rows), ZERO)

    @property
    def flow_effect(self) -> Decimal:
        return sum((r.flow_effect for r in self.rows), ZERO)

    @property
    def cost_added(self) -> Decimal:
        return sum((r.cost_added for r in self.rows), ZERO)


class SnapshotService:
    def __init__(
        self,
        snapshots: SnapshotRepository,
        portfolio: PortfolioService,
        today: Callable[[], date] = date.today,
        asset_snapshots: AssetSnapshotRepository | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.portfolio = portfolio
        self.today = today
        self.asset_snapshots = asset_snapshots

    def record(self, summary: PortfolioSummary) -> Snapshot | None:
        """Bugünün değerini (toplam ve varlık bazında) kaydeder; aynı gün tekrar çağrılırsa günceller."""
        if summary.total_value == 0 and summary.total_cost == 0:
            return None
        day = self.today()
        snap = Snapshot(day, summary.total_value, summary.total_cost, summary.base_currency)
        self.snapshots.upsert(snap)
        if self.asset_snapshots is not None:
            rows = []
            for pv in summary.open_positions:
                value, cost = pv.market_value_base, pv.cost_basis_base
                if value is None or cost is None or pv.asset.id is None or not pv.state.quantity:
                    continue
                rows.append(
                    AssetSnapshot(
                        day,
                        pv.asset.id,
                        pv.state.quantity,
                        value / pv.state.quantity,
                        value,
                        cost,
                        summary.base_currency,
                    )
                )
            self.asset_snapshots.replace_day(day, summary.base_currency, rows)
        return snap

    # ------------------------------------------------------------------ ne değişti?
    def snapshot_dates(self, currency: Currency) -> list[date]:
        return self.asset_snapshots.dates(currency) if self.asset_snapshots is not None else []

    def changes(self, currency: Currency, since: date, until: date | None = None) -> ChangeReport | None:
        """`since` kaydından `until` (varsayılan: en son) kaydına varlık bazında değişim."""
        if self.asset_snapshots is None:
            return None
        dates = self.asset_snapshots.dates(currency)
        if not dates:
            return None
        until = until or dates[-1]
        before = {r.asset_id: r for r in self.asset_snapshots.on(since, currency)}
        after = {r.asset_id: r for r in self.asset_snapshots.on(until, currency)}
        if not before and not after:
            return None
        assets = {a.id: a for a in self.portfolio.assets.list()}
        rows: list[ChangeRow] = []
        for aid in before.keys() | after.keys():
            asset = assets.get(aid)
            if asset is None:
                continue
            b, a = before.get(aid), after.get(aid)
            vb, va = (b.value if b else ZERO), (a.value if a else ZERO)
            price_effect = b.quantity * (a.price - b.price) if a and b else ZERO
            rows.append(
                ChangeRow(
                    asset,
                    vb,
                    va,
                    b.price if b else None,
                    a.price if a else None,
                    price_effect,
                    va - vb - price_effect,
                    (a.cost if a else ZERO) - (b.cost if b else ZERO),
                )
            )
        rows.sort(key=lambda r: r.change)
        return ChangeReport(since, until, rows)

    def history(self, currency: Currency) -> list[Snapshot]:
        return self.snapshots.list(currency)

    def backfill(self, base: Currency, *, max_days: int = 3 * 365) -> int:
        """İşlem defteri + geçmiş fiyatlarla ilk işlemden bugüne günlük değeri yeniden hesaplar.

        Sadece işlem günleri (fiyat verisi olan günler) yazılır. Döndürülen değer: yazılan gün sayısı.
        """
        assets = self.portfolio.assets.list()
        grouped = self.portfolio.transactions.by_asset()
        dated = [t.trade_date for txs in grouped.values() for t in txs]
        if not dated:
            return 0
        today = self.today()
        start = max(min(dated), today - timedelta(days=max_days))
        traded = [a for a in assets if a.id in grouped]
        market = self.portfolio.market
        prices = market.price_history(traded, start - timedelta(days=7))
        quote_ccy = {a.id: a.currency for a in traded}
        fx = market.fx_history(set(quote_ccy.values()), base, start - timedelta(days=7))

        trading_days = sorted({d for series in prices.values() for d in series if start <= d < today})
        snaps: list[Snapshot] = []
        for day in trading_days:
            value = cost = ZERO
            complete = True
            for asset in traded:
                state = compute_position(grouped[asset.id or -1], as_of=day)
                if not state.is_open:
                    continue
                price = value_on(prices.get(asset.id or -1, {}), day)
                rate = value_on(fx.get(asset.currency, {}), day)
                if price is None or rate is None:
                    complete = False
                    break
                value += state.quantity * price * rate
                cost += state.cost_basis * rate
            if complete and (value or cost):
                snaps.append(Snapshot(day, value, cost, base))
        self.snapshots.upsert_many(snaps)
        return len(snaps)


def snapshots_to_rows(snaps: list[Snapshot]) -> list[dict[str, object]]:
    return [
        {
            "Tarih": s.snapshot_date,
            "Değer": float(s.total_value),
            "Maliyet": float(s.total_cost),
            "K/Z": float(s.total_value - s.total_cost),
        }
        for s in snaps
    ]
