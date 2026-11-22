"""Hedefler: düzenli alım planı (ör. aylık gram altın), pasif gelir hedefi ve enflasyon beklentisi."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from divitrack.core.calculations import compute_position
from divitrack.core.entities import Asset
from divitrack.core.enums import Currency, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.core.gold_plan import BuyPlan, PlanStatus, evaluate_plan
from divitrack.core.money import ZERO
from divitrack.repositories.sqlite import SettingsRepository
from divitrack.services.analytics_service import EXPECTED_CPI_KEY
from divitrack.services.dividend_engine import DividendEngine, EventKind
from divitrack.services.portfolio_service import PortfolioService

BUY_PLAN_KEY = "plan.buy_plan"
PASSIVE_TARGET_KEY = "goal.passive_income_monthly"


@dataclass(frozen=True, slots=True)
class PassiveIncome:
    target_monthly: Decimal | None
    next_12m_net: Decimal  # önümüzdeki 12 ay net temettü (TL)
    paying_value: Decimal  # temettü ödeyen kalemlerin değeri (TL)

    @property
    def monthly(self) -> Decimal:
        return self.next_12m_net / 12

    @property
    def progress_pct(self) -> Decimal | None:
        if not self.target_monthly:
            return None
        return self.monthly / self.target_monthly * 100

    @property
    def yield_pct(self) -> Decimal | None:
        """Temettü ödeyen kalemlerin net verimi."""
        return self.next_12m_net / self.paying_value * 100 if self.paying_value > 0 else None

    @property
    def gap_monthly(self) -> Decimal | None:
        return max(ZERO, self.target_monthly - self.monthly) if self.target_monthly else None

    @property
    def capital_needed(self) -> Decimal | None:
        """Kalan aylık açığı bugünkü verimle kapatmak için gereken ek yatırım."""
        y = self.yield_pct
        gap = self.gap_monthly
        if gap is None or not y:
            return None
        return gap * 12 / (y / 100)


class PlanService:
    def __init__(
        self,
        portfolio: PortfolioService,
        dividends: DividendEngine,
        settings: SettingsRepository,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.portfolio = portfolio
        self.dividends = dividends
        self.settings = settings
        self.today = today

    # ------------------------------------------------------------------ alım planı
    def buy_plan(self) -> BuyPlan | None:
        raw = self.settings.get(BUY_PLAN_KEY)
        if not raw:
            return None
        d = json.loads(raw)
        return BuyPlan(
            d["symbol"],
            Decimal(d["monthly_quantity"]),
            d["start_month"],
            Decimal(d["dip_price"]) if d.get("dip_price") else None,
            Decimal(d.get("dip_extra") or "0"),
        )

    def save_buy_plan(self, plan: BuyPlan | None) -> None:
        if plan is None:
            self.settings.set(BUY_PLAN_KEY, "")
            return
        self.settings.set(
            BUY_PLAN_KEY,
            json.dumps(
                {
                    "symbol": plan.symbol,
                    "monthly_quantity": str(plan.monthly_quantity),
                    "start_month": plan.start_month,
                    "dip_price": str(plan.dip_price) if plan.dip_price is not None else None,
                    "dip_extra": str(plan.dip_extra),
                }
            ),
        )

    def plan_asset(self, plan: BuyPlan) -> Asset | None:
        return next((a for a in self.portfolio.assets.list() if a.symbol == plan.symbol.upper()), None)

    def buy_plan_status(self) -> tuple[BuyPlan, Asset, PlanStatus] | None:
        plan = self.buy_plan()
        if plan is None:
            return None
        asset = self.plan_asset(plan)
        if asset is None or asset.id is None:
            raise ValidationError(f"Plandaki varlık ({plan.symbol}) kayıtlı değil. Önce varlığı ekle.")
        txs = self.portfolio.transactions.list(asset.id)
        y, m = map(int, plan.start_month.split("-"))
        start = date(y, m, 1)
        before = compute_position(txs, as_of=start - timedelta(days=1)) if txs else None
        held = (before.quantity, before.cost_basis) if before else (ZERO, ZERO)
        buys = [
            (t.trade_date, t.quantity, t.gross_amount + t.fee)
            for t in txs
            if t.trade_date >= start and t.tx_type in (TxType.BUY, TxType.REINVEST, TxType.RIGHTS)
        ]
        quote = self.portfolio.market.quotes_for([asset]).get(asset.id)
        return plan, asset, evaluate_plan(plan, buys, held, self.today(), quote.price if quote else None)

    # ------------------------------------------------------------------ pasif gelir
    def passive_target(self) -> Decimal | None:
        raw = self.settings.get(PASSIVE_TARGET_KEY)
        return Decimal(raw) if raw else None

    def set_passive_target(self, monthly: Decimal | None) -> None:
        if monthly is not None and monthly <= 0:
            raise ValidationError("Aylık hedef sıfırdan büyük olmalı")
        self.settings.set(PASSIVE_TARGET_KEY, "" if monthly is None else str(monthly))

    def passive_income(self) -> PassiveIncome:
        today = self.today()
        positions = self.portfolio.positions()
        events = [
            e
            for e in self.dividends.events(positions)
            if e.kind is not EventKind.PAID and today <= e.pay_date <= today + timedelta(days=365)
        ]
        rates = self.portfolio.market.fx_rates({e.currency for e in events}, Currency.TRY)
        net = sum((e.net * rates[e.currency] for e in events if e.currency in rates), ZERO)
        paying = {e.asset_id for e in events}
        summary = self.portfolio.summary(Currency.TRY)
        value = sum(
            (pv.market_value_base or ZERO for pv in summary.open_positions if pv.asset.id in paying),
            ZERO,
        )
        return PassiveIncome(self.passive_target(), net, value)

    # ------------------------------------------------------------------ enflasyon beklentisi
    def expected_cpi(self) -> Decimal | None:
        raw = self.settings.get(EXPECTED_CPI_KEY)
        return Decimal(raw) if raw else None

    def set_expected_cpi(self, pct: Decimal | None) -> None:
        if pct is not None and not Decimal(-50) < pct < Decimal(1000):
            raise ValidationError("Beklenen enflasyon makul bir yüzde olmalı")
        self.settings.set(EXPECTED_CPI_KEY, "" if pct is None else str(pct))
