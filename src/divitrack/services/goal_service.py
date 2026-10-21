"""Birikim hedefleri: ilerleme, kalan süre, aylık gereken tutar ve mevcut tasarruf hızıyla karşılaştırma."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from divitrack.core.entities import Goal
from divitrack.core.enums import Currency
from divitrack.core.money import HUNDRED, ZERO
from divitrack.repositories.sqlite import GoalRepository
from divitrack.services.budget_service import BudgetService, add_months, month_start
from divitrack.services.portfolio_service import PortfolioService


def months_between(start: date, end: date) -> int:
    """`start` ayından `end` ayına kalan tam ay sayısı (geçmişse 0)."""
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


@dataclass(frozen=True, slots=True)
class GoalProgress:
    goal: Goal
    saved: Decimal  # TL
    months_left: int
    pace: Decimal | None  # son ayların ortalama yatırılabilir tutarı (TL/ay)
    symbols: tuple[str, ...]

    @property
    def remaining(self) -> Decimal:
        return max(ZERO, self.goal.target_amount - self.saved)

    @property
    def pct(self) -> Decimal:
        return min(HUNDRED, self.saved / self.goal.target_amount * HUNDRED)

    @property
    def reached(self) -> bool:
        return self.saved >= self.goal.target_amount

    @property
    def required_monthly(self) -> Decimal | None:
        if self.reached:
            return None
        return self.remaining / self.months_left if self.months_left > 0 else self.remaining

    @property
    def projected(self) -> Decimal | None:
        return None if self.pace is None else self.saved + self.pace * self.months_left

    @property
    def status(self) -> str:
        if self.reached:
            return "Ulaşıldı"
        if self.months_left == 0:
            return "Süre doldu"
        if self.pace is None:
            return "Tasarruf verisi yok"
        return "Yolunda" if (self.projected or ZERO) >= self.goal.target_amount else "Geride"

    @property
    def months_needed(self) -> int | None:
        """Mevcut hızla hedefe kaç ayda ulaşılır (hız sıfırsa None)."""
        if self.reached:
            return 0
        if not self.pace or self.pace <= 0:
            return None
        whole, frac = divmod(self.remaining, self.pace)
        return int(whole) + (1 if frac else 0)


class GoalService:
    def __init__(
        self,
        goals: GoalRepository,
        portfolio: PortfolioService,
        budget: BudgetService,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.goals = goals
        self.portfolio = portfolio
        self.budget = budget
        self.today = today

    def list(self) -> list[Goal]:
        return self.goals.list()

    def add(self, goal: Goal) -> Goal:
        self._check_assets(goal)
        return self.goals.add(goal)

    def update(self, goal: Goal) -> Goal:
        self._check_assets(goal)
        return self.goals.update(goal)

    def delete(self, goal_id: int) -> None:
        self.goals.delete(goal_id)

    def _check_assets(self, goal: Goal) -> None:
        for aid in goal.asset_ids:
            self.portfolio.assets.get(aid)

    def monthly_pace(self, months: int = 3) -> Decimal | None:
        """Son tamamlanmış `months` ayın ortalama yatırılabilir tutarı (net tasarruf × yatırım payı)."""
        last_full = add_months(month_start(self.today()), -1)
        history = [m for m in self.budget.history(months, until=last_full) if m.flow_count]
        if not history:
            return None
        net = sum((max(ZERO, m.net) for m in history), ZERO) / len(history)
        return net * self.budget.invest_share

    def progress(self, *, force_refresh: bool = False) -> list[GoalProgress]:
        goals = self.goals.list()
        if not goals:
            return []
        summary = self.portfolio.summary(Currency.TRY, force_refresh=force_refresh)
        values = {pv.asset.id: pv.market_value_base or ZERO for pv in summary.open_positions}
        symbols = {pv.asset.id: pv.asset.symbol for pv in summary.positions}
        pace = self.monthly_pace()
        today = self.today()
        return [
            GoalProgress(
                g,
                sum((values.get(aid, ZERO) for aid in g.asset_ids), ZERO) + g.extra_saved,
                months_between(today, g.target_date),
                pace,
                tuple(symbols[aid] for aid in g.asset_ids if aid in symbols),
            )
            for g in goals
        ]
