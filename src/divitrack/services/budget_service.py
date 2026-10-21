"""Bütçe servisi: gelir/gider kayıtları, kategori bütçeleri, tasarruf oranı ve yatırılabilir tutar."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from divitrack.core.entities import CashFlow, Category
from divitrack.core.enums import FlowKind
from divitrack.core.exceptions import ValidationError
from divitrack.core.money import HUNDRED, ONE, ZERO, pct
from divitrack.repositories.sqlite import CashFlowRepository, CategoryRepository, SettingsRepository

SAVINGS_TARGET_KEY = "budget.savings_target"
INVEST_SHARE_KEY = "budget.invest_share"
DEFAULT_SAVINGS_TARGET = Decimal("0.20")
DEFAULT_INVEST_SHARE = ONE


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, months: int) -> date:
    index = d.year * 12 + (d.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


@dataclass(frozen=True, slots=True)
class CategoryLine:
    category: Category
    actual: Decimal

    @property
    def budget(self) -> Decimal | None:
        return self.category.monthly_budget

    @property
    def remaining(self) -> Decimal | None:
        return None if self.budget is None else self.budget - self.actual

    @property
    def used_pct(self) -> Decimal | None:
        return None if not self.budget else self.actual / self.budget * HUNDRED

    @property
    def over_budget(self) -> bool:
        return self.budget is not None and self.actual > self.budget


@dataclass(slots=True)
class MonthSummary:
    month: date
    income: Decimal = ZERO
    expense: Decimal = ZERO
    lines: list[CategoryLine] = field(default_factory=list)
    flow_count: int = 0

    @property
    def net(self) -> Decimal:
        return self.income - self.expense

    @property
    def savings_rate(self) -> Decimal | None:
        """Net tasarruf / gelir (%)."""
        return pct(self.net, self.income)

    @property
    def budget_total(self) -> Decimal:
        return sum((ln.budget or ZERO for ln in self.lines if ln.category.kind is FlowKind.EXPENSE), ZERO)

    @property
    def over_budget(self) -> list[CategoryLine]:
        return [ln for ln in self.lines if ln.over_budget]

    @property
    def label(self) -> str:
        return f"{self.month.year}-{self.month.month:02d}"


class BudgetService:
    def __init__(
        self,
        categories: CategoryRepository,
        flows: CashFlowRepository,
        settings: SettingsRepository,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.categories = categories
        self.flows = flows
        self.settings = settings
        self.today = today

    # ------------------------------------------------------------------ ayarlar
    def _ratio(self, key: str, default: Decimal) -> Decimal:
        raw = self.settings.get(key)
        return Decimal(raw) if raw is not None else default

    def _set_ratio(self, key: str, value: Decimal) -> None:
        if not ZERO <= value <= ONE:
            raise ValidationError("Oran 0 ile 1 arasında olmalı")
        self.settings.set(key, str(value))

    @property
    def savings_target(self) -> Decimal:
        """Hedef tasarruf oranı (0–1)."""
        return self._ratio(SAVINGS_TARGET_KEY, DEFAULT_SAVINGS_TARGET)

    def set_savings_target(self, value: Decimal) -> None:
        self._set_ratio(SAVINGS_TARGET_KEY, value)

    @property
    def invest_share(self) -> Decimal:
        """Aylık net tasarrufun yatırıma ayrılan kısmı (0–1); kalanı acil durum/nakit için."""
        return self._ratio(INVEST_SHARE_KEY, DEFAULT_INVEST_SHARE)

    def set_invest_share(self, value: Decimal) -> None:
        self._set_ratio(INVEST_SHARE_KEY, value)

    # ------------------------------------------------------------------ kategoriler
    def list_categories(self, kind: FlowKind | None = None) -> list[Category]:
        cats = self.categories.list()
        return [c for c in cats if kind is None or c.kind is kind]

    def add_category(self, category: Category) -> Category:
        if category.kind is FlowKind.INCOME:
            category.monthly_budget = None
        return self.categories.add(category)

    def set_budget(self, category_id: int, amount: Decimal | None) -> Category:
        cat = self.categories.get(category_id)
        if cat.kind is FlowKind.INCOME and amount is not None:
            raise ValidationError("Bütçe yalnızca gider kategorilerine tanımlanır")
        if amount is not None and amount < 0:
            raise ValidationError("Bütçe negatif olamaz")
        cat.monthly_budget = amount if amount else None
        return self.categories.update(cat)

    def rename_category(self, category_id: int, name: str) -> Category:
        cat = self.categories.get(category_id)
        return self.categories.update(Category(name, cat.kind, cat.monthly_budget, cat.id))

    def delete_category(self, category_id: int) -> None:
        self.categories.delete(category_id)

    def get_or_create_category(self, name: str, kind: FlowKind) -> Category:
        existing = self.categories.find(name)
        if existing is not None:
            if existing.kind is not kind:
                raise ValidationError(f"'{existing.name}' bir {existing.kind.label.lower()} kategorisi")
            return existing
        return self.add_category(Category(name, kind))

    # ------------------------------------------------------------------ kayıtlar
    def add_flow(self, flow: CashFlow) -> CashFlow:
        self.categories.get(flow.category_id)  # kategori var mı
        return self.flows.add(flow)

    def delete_flow(self, flow_id: int) -> None:
        self.flows.delete(flow_id)

    def flows_in_month(self, month: date) -> list[CashFlow]:
        start = month_start(month)
        return self.flows.list(start, add_months(start, 1))

    # ------------------------------------------------------------------ özetler
    def month_summary(self, month: date) -> MonthSummary:
        return self._summarize(month_start(month), self.flows_in_month(month), self.categories.list())

    def history(self, months: int = 12, *, until: date | None = None) -> list[MonthSummary]:
        """Son `months` ayın özetleri (eskiden yeniye), `until` ayı dahil."""
        last = month_start(until or self.today())
        first = add_months(last, -(months - 1))
        flows = self.flows.list(first, add_months(last, 1))
        cats = self.categories.list()
        by_month: dict[date, list[CashFlow]] = defaultdict(list)
        for f in flows:
            by_month[month_start(f.flow_date)].append(f)
        return [
            self._summarize(add_months(first, i), by_month.get(add_months(first, i), []), cats) for i in range(months)
        ]

    @staticmethod
    def _summarize(month: date, flows: list[CashFlow], cats: list[Category]) -> MonthSummary:
        by_id = {c.id: c for c in cats}
        actual: dict[int, Decimal] = defaultdict(lambda: ZERO)
        summary = MonthSummary(month=month, flow_count=len(flows))
        for f in flows:
            cat = by_id.get(f.category_id)
            if cat is None:
                continue
            actual[f.category_id] += f.amount
            if cat.kind is FlowKind.INCOME:
                summary.income += f.amount
            else:
                summary.expense += f.amount
        summary.lines = [
            CategoryLine(c, actual.get(c.id or -1, ZERO))
            for c in cats
            if c.id in actual or (c.kind is FlowKind.EXPENSE and c.monthly_budget)
        ]
        summary.lines.sort(key=lambda ln: (ln.category.kind is FlowKind.EXPENSE, -ln.actual))
        return summary

    def average_savings_rate(self, months: int = 3, *, until: date | None = None) -> Decimal | None:
        """Geliri olan son `months` ayın toplam tasarruf oranı (%). Veri yoksa None."""
        hist = [m for m in self.history(months, until=until) if m.income > 0]
        if not hist:
            return None
        income = sum((m.income for m in hist), ZERO)
        net = sum((m.net for m in hist), ZERO)
        return pct(net, income)

    def investable_amount(self, month: date) -> Decimal:
        """Ayın net tasarrufunun yatırıma ayrılan kısmı (negatifse 0)."""
        net = self.month_summary(month).net
        return max(ZERO, net) * self.invest_share
