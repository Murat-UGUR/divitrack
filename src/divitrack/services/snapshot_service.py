"""Portföy değer geçmişi: günlük anlık görüntü ve geçmişe dönük yeniden oluşturma."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from divitrack.core.calculations import compute_position
from divitrack.core.entities import Snapshot
from divitrack.core.enums import Currency
from divitrack.core.money import ZERO
from divitrack.repositories.sqlite import SnapshotRepository
from divitrack.services.market.market_data_service import value_on
from divitrack.services.portfolio_service import PortfolioService, PortfolioSummary


class SnapshotService:
    def __init__(
        self,
        snapshots: SnapshotRepository,
        portfolio: PortfolioService,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.snapshots = snapshots
        self.portfolio = portfolio
        self.today = today

    def record(self, summary: PortfolioSummary) -> Snapshot | None:
        """Bugünün değerini kaydeder (aynı gün tekrar çağrılırsa günceller)."""
        if summary.total_value == 0 and summary.total_cost == 0:
            return None
        snap = Snapshot(self.today(), summary.total_value, summary.total_cost, summary.base_currency)
        self.snapshots.upsert(snap)
        return snap

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
