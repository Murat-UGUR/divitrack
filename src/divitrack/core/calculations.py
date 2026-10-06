"""Saf hesaplama fonksiyonları: maliyet, pozisyon, temettü projeksiyonu."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from decimal import Decimal

from divitrack.core.entities import PositionState, Transaction
from divitrack.core.enums import TxType
from divitrack.core.exceptions import InsufficientQuantityError
from divitrack.core.money import ZERO


def _ordered(transactions: Iterable[Transaction]) -> list[Transaction]:
    return sorted(transactions, key=lambda t: (t.trade_date, t.id or 0))


def compute_position(transactions: Iterable[Transaction], *, as_of: date | None = None) -> PositionState:
    """Ağırlıklı ortalama maliyet yöntemiyle pozisyonu hesaplar.

    - Alış komisyonu maliyete eklenir.
    - Satışta gerçekleşen K/Z = (satış tutarı - komisyon) - (ortalama maliyet × adet).
    - Pozisyon tamamen kapanınca ortalama maliyet sıfırlanır.
    """
    state = PositionState()
    for tx in _ordered(transactions):
        if as_of is not None and tx.trade_date > as_of:
            break
        if state.first_trade is None:
            state.first_trade = tx.trade_date
        state.fees_paid += tx.fee
        if tx.tx_type is TxType.BUY:
            state.cost_basis += tx.gross_amount + tx.fee
            state.quantity += tx.quantity
            state.avg_cost = state.cost_basis / state.quantity
        else:
            if tx.quantity > state.quantity:
                raise InsufficientQuantityError(
                    f"{tx.trade_date} tarihinde {tx.quantity} adet satılamaz; eldeki adet {state.quantity}"
                )
            released_cost = state.avg_cost * tx.quantity
            state.realized_pnl += tx.gross_amount - tx.fee - released_cost
            state.quantity -= tx.quantity
            state.cost_basis -= released_cost
            if state.quantity == 0:
                state.cost_basis = ZERO
                state.avg_cost = ZERO
    return state


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 Şubat
        return d.replace(year=d.year + years, day=28)


def trailing_payments(
    payments: Sequence[tuple[date, Decimal]], today: date, days: int = 365
) -> list[tuple[date, Decimal]]:
    start = today - timedelta(days=days)
    return [(d, a) for d, a in payments if start < d <= today]


def ttm_dividend_per_share(payments: Sequence[tuple[date, Decimal]], today: date) -> Decimal:
    return sum((a for _, a in trailing_payments(payments, today)), ZERO)


def payment_frequency(payments: Sequence[tuple[date, Decimal]], today: date) -> int:
    """Son 12 aydaki ödeme sayısı (12=aylık, 4=çeyreklik, 2=yarıyıllık, 1=yıllık)."""
    return len(trailing_payments(payments, today))


FREQUENCY_LABELS = {0: "—", 1: "Yıllık", 2: "Yarıyıllık", 3: "Yılda 3", 4: "Çeyreklik", 12: "Aylık"}


def frequency_label(count: int) -> str:
    return FREQUENCY_LABELS.get(count, f"Yılda {count}")


def project_payments(
    payments: Sequence[tuple[date, Decimal]], today: date, horizon_days: int = 365
) -> list[tuple[date, Decimal]]:
    """Son 12 ayın ödeme takvimini bir yıl ileri kaydırarak gelecek ödemeleri tahmin eder.

    Basit ama yaygın kullanılan yöntem: şirketin geçen yılki takvimi ve tutarı tekrarlanır.
    """
    horizon = today + timedelta(days=horizon_days)
    projected: list[tuple[date, Decimal]] = []
    for paid_on, amount in trailing_payments(payments, today):
        nxt = add_years(paid_on, 1)
        while nxt <= today:
            nxt = add_years(nxt, 1)
        if nxt <= horizon:
            projected.append((nxt, amount))
    return sorted(projected)
