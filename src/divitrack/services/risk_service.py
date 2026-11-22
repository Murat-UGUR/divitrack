"""Risk: portföyün para girişlerinden arındırılmış zirveden düşüşü, varlık oynaklığı ve korelasyonlar."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from divitrack.core.entities import Asset
from divitrack.core.enums import AssetClass, Currency
from divitrack.core.money import ZERO
from divitrack.core.risk import Drawdown, correlation, daily_returns, drawdown, performance_index, volatility
from divitrack.repositories.sqlite import SnapshotRepository
from divitrack.services.market.market_data_service import value_on
from divitrack.services.portfolio_service import PortfolioService


@dataclass(frozen=True, slots=True)
class AssetRisk:
    asset: Asset
    weight_pct: Decimal
    volatility_pct: Decimal | None  # yıllık, TL bazında


@dataclass(slots=True)
class RiskReport:
    drawdown: Drawdown | None
    index: list[tuple[date, float]]  # para girişinden arındırılmış performans endeksi (ilk gün 100)
    assets: list[AssetRisk] = field(default_factory=list)
    correlations: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    portfolio_volatility_pct: Decimal | None = None
    days: int = 365

    def correlation(self, a: str, b: str) -> Decimal | None:
        if a == b:
            return Decimal(1)
        return self.correlations.get((a, b)) or self.correlations.get((b, a))


class RiskService:
    def __init__(
        self, snapshots: SnapshotRepository, portfolio: PortfolioService, today: Callable[[], date] = date.today
    ) -> None:
        self.snapshots = snapshots
        self.portfolio = portfolio
        self.today = today

    def report(self, base: Currency = Currency.TRY, *, days: int = 365) -> RiskReport:
        snaps = self.snapshots.list(base)
        index = performance_index([(s.snapshot_date, s.total_value, s.total_cost) for s in snaps])
        report = RiskReport(drawdown(index), index, days=days)

        summary = self.portfolio.summary(Currency.TRY)
        total = summary.total_value
        open_views = [pv for pv in summary.open_positions if pv.market_value_base is not None]
        if not open_views or total <= 0:
            return report
        start = self.today() - timedelta(days=days)
        market = self.portfolio.market
        assets = [pv.asset for pv in open_views]
        prices = market.price_history(assets, start - timedelta(days=7))
        fx = market.fx_history({a.currency for a in assets}, Currency.TRY, start - timedelta(days=7))

        returns: dict[str, dict[date, float]] = {}
        weights: dict[str, float] = {}
        for pv in open_views:
            a = pv.asset
            assert a.id is not None and pv.market_value_base is not None
            weight = pv.market_value_base / total * 100
            vol: Decimal | None = None
            if a.asset_class is not AssetClass.CASH and a.manual_price is None:
                series = prices.get(a.id, {})
                rates = fx.get(a.currency, {})
                try_series = {}
                for d, p in series.items():
                    if d < start:
                        continue
                    rate = value_on(rates, d) if a.currency is not Currency.TRY else Decimal(1)
                    if rate is not None:
                        try_series[d] = p * rate
                r = daily_returns(try_series)
                vol = volatility(r)
                if vol is not None:
                    returns[a.symbol] = r
                    weights[a.symbol] = float(weight)
            report.assets.append(AssetRisk(a, weight, vol))
        report.assets.sort(key=lambda r: -r.weight_pct)

        symbols = list(returns)
        for i, x in enumerate(symbols):
            for y in symbols[i + 1 :]:
                c = correlation(returns[x], returns[y])
                if c is not None:
                    report.correlations[(x, y)] = c

        # Portföy oynaklığı: fiyatlı kalemlerin bugünkü ağırlıklarıyla günlük getiri (nakit/sabit = 0 getiri)
        if returns:
            common = sorted(set.intersection(*(set(r) for r in returns.values())))
            port = {d: sum(weights[s] / 100 * returns[s][d] for s in symbols) for d in common}
            report.portfolio_volatility_pct = volatility(port)
        return report


def concentration_pct(report: RiskReport, top: int = 3) -> Decimal:
    """En büyük `top` kalemin toplam payı (%)."""
    return sum((r.weight_pct for r in report.assets[:top]), ZERO)
