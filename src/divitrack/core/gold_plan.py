"""Düzenli (aylık) alım planı: plan / gerçekleşen karşılaştırması ve ortalama maliyetin ay ay seyri."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from divitrack.core.exceptions import ValidationError
from divitrack.core.money import ZERO


@dataclass(frozen=True, slots=True)
class BuyPlan:
    """`monthly_quantity` adet/gram her ay; fiyat `dip_price` altındaysa `dip_extra` kadar ek alım."""

    symbol: str
    monthly_quantity: Decimal
    start_month: str  # "YYYY-AA"
    dip_price: Decimal | None = None
    dip_extra: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.monthly_quantity <= 0:
            raise ValidationError("Aylık miktar sıfırdan büyük olmalı")
        if self.dip_price is not None and self.dip_price <= 0:
            raise ValidationError("Ek alım eşiği sıfırdan büyük olmalı")
        if self.dip_extra < 0:
            raise ValidationError("Ek alım miktarı negatif olamaz")
        y, m = self.start_month.split("-")
        if not (1 <= int(m) <= 12 and len(y) == 4):
            raise ValidationError("Başlangıç ayı YYYY-AA biçiminde olmalı")


@dataclass(frozen=True, slots=True)
class PlanMonth:
    month: str
    planned: Decimal  # o ay planlanan
    bought: Decimal  # o ay alınan
    spent: Decimal  # o ay harcanan (komisyon dahil)
    cum_planned: Decimal
    cum_bought: Decimal
    average_after: Decimal | None  # ay sonundaki ortalama maliyet (tüm elde tutulan)

    @property
    def done(self) -> bool:
        return self.bought >= self.planned


@dataclass(frozen=True, slots=True)
class PlanStatus:
    months: list[PlanMonth]
    this_month_remaining: Decimal
    dip_active: bool  # fiyat eşiğin altında: ek alım önerilir
    suggested: Decimal  # bu ay alınması önerilen toplam (kalan + ek)
    behind: Decimal  # plana göre birikmiş eksik (artı) / fazla (eksi)
    price_now: Decimal | None

    @property
    def suggested_amount(self) -> Decimal | None:
        return self.suggested * self.price_now if self.price_now is not None else None


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out


def evaluate_plan(
    plan: BuyPlan,
    buys: Sequence[tuple[date, Decimal, Decimal]],
    held_before_start: tuple[Decimal, Decimal],
    today: date,
    price_now: Decimal | None,
) -> PlanStatus:
    """`buys`: plan varlığının (tarih, adet, tutar) alımları; `held_before_start`: başlangıçtan önceki (adet, maliyet).

    Ortalama, satışları yok sayan basit bir seyirdir (plan izleme için).
    """
    current = _month_key(today)
    months = _months(plan.start_month, current) if plan.start_month <= current else []
    by_month: dict[str, tuple[Decimal, Decimal]] = {}
    for d, q, amount in buys:
        k = _month_key(d)
        q0, a0 = by_month.get(k, (ZERO, ZERO))
        by_month[k] = (q0 + q, a0 + amount)
    qty, cost = held_before_start
    cum_p = cum_b = ZERO
    rows: list[PlanMonth] = []
    for k in months:
        q, a = by_month.get(k, (ZERO, ZERO))
        qty += q
        cost += a
        cum_p += plan.monthly_quantity
        cum_b += q
        rows.append(PlanMonth(k, plan.monthly_quantity, q, a, cum_p, cum_b, cost / qty if qty > 0 else None))
    remaining = max(ZERO, plan.monthly_quantity - (rows[-1].bought if rows and rows[-1].month == current else ZERO))
    dip = plan.dip_price is not None and price_now is not None and price_now <= plan.dip_price
    return PlanStatus(
        months=rows,
        this_month_remaining=remaining if months else ZERO,
        dip_active=dip,
        suggested=(remaining if months else ZERO) + (plan.dip_extra if dip else ZERO),
        behind=cum_p - cum_b,
        price_now=price_now,
    )
