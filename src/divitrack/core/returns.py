"""Alım (lot) bazında maliyet takibi ve para ağırlıklı yıllık getiri (saf fonksiyonlar).

`open_lots` işlem defterini FIFO ile işler: her alış bir lot açar, satışlar en eski lottan düşer,
bedelsiz/bölünme eldeki tüm lotların adedini aynı oranda artırır (maliyet değişmez).
`xirr` düzensiz tarihli nakit akışlarının yıllık iç verim oranını bulur.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from divitrack.core.entities import Transaction
from divitrack.core.enums import TxType
from divitrack.core.money import ONE, ZERO


@dataclass(slots=True)
class OpenLot:
    """Elde kalan bir alım. Tutarlar varlığın kendi para birimindedir.

    `cost` kalan adede düşen maliyettir (komisyon dahil). Bedelsizden sonra `quantity` artar,
    `cost` aynı kalır; satışta ikisi de aynı oranda azalır.
    """

    trade: Transaction
    quantity: Decimal
    cost: Decimal

    @property
    def original_quantity(self) -> Decimal:
        return self.trade.quantity


def open_lots(transactions: Sequence[Transaction]) -> list[OpenLot]:
    """İşlem defterinden elde kalan lotlar (en eskiden en yeniye)."""
    lots: list[OpenLot] = []
    for t in sorted(transactions, key=lambda t: (t.trade_date, t.id or 0)):
        if t.tx_type is TxType.SELL:
            remaining = t.quantity
            while remaining > 0 and lots:
                lot = lots[0]
                take = min(lot.quantity, remaining)
                lot.cost -= lot.cost * take / lot.quantity
                lot.quantity -= take
                remaining -= take
                if lot.quantity <= 0:
                    lots.pop(0)
        elif t.tx_type is TxType.BONUS:
            held = sum((lot.quantity for lot in lots), ZERO)
            if held > 0:
                factor = (held + t.quantity) / held
                for lot in lots:
                    lot.quantity *= factor
        else:  # BUY, RIGHTS, REINVEST
            lots.append(OpenLot(t, t.quantity, t.gross_amount + t.fee))
    return lots


def xirr(flows: Sequence[tuple[date, Decimal]], *, tol: float = 1e-7, max_iter: int = 200) -> Decimal | None:
    """Yıllık iç verim oranı (ör. 0.42 = yıllık %42). Akışlar: yatırılan para eksi, alınan/değer artı.

    Hem eksi hem artı akış yoksa veya kök bulunamazsa None döner. İkiye bölme (bisection) kullanılır;
    yavaş ama her zaman yakınsar.
    """
    if not flows:
        return None
    values = [(d, float(v)) for d, v in flows if v != 0]
    if not any(v < 0 for _, v in values) or not any(v > 0 for _, v in values):
        return None
    start = min(d for d, _ in values)
    if max(d for d, _ in values) == start:
        return None

    def npv(rate: float) -> float:
        return sum(v / (1 + rate) ** ((d - start).days / 365.0) for d, v in values)

    low, high = -0.9999, 10.0
    f_low, f_high = npv(low), npv(high)
    if f_low * f_high > 0:
        return None
    for _ in range(max_iter):
        mid = (low + high) / 2
        f_mid = npv(mid)
        if abs(f_mid) < tol or (high - low) / 2 < tol:
            return Decimal(str(round(mid, 6)))
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return Decimal(str(round((low + high) / 2, 6)))


def real_rate(nominal: Decimal, inflation: Decimal) -> Decimal:
    """Nominal getiri ve enflasyondan reel getiri (oranlar, ör. 0.40 ve 0.30 → 0.0769)."""
    return (ONE + nominal) / (ONE + inflation) - ONE
