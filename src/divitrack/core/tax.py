"""Yurt dışı hisse/ETF satış kazancı ön izlemesi (saf fonksiyonlar).

Değer artış kazancında maliyet, alış ayından önceki ay ile satış ayından önceki ay arasındaki Yİ-ÜFE artışı
%10 veya fazlaysa bu oranda artırılır (endeksleme). TL karşılıkları alış ve satış günlerinin kuruyla bulunur.
Bu bir ön izlemedir: kesin tutar için bir mali müşavire danışın.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from divitrack.core.entities import Transaction
from divitrack.core.enums import TxType
from divitrack.core.money import ONE, ZERO

INDEX_THRESHOLD = Decimal("0.10")


@dataclass(frozen=True, slots=True)
class RealizedLot:
    """Bir satışın FIFO ile eşleştiği alış parçası (varlığın kendi para biriminde)."""

    buy_date: date
    sell_date: date
    quantity: Decimal
    cost: Decimal  # alış tutarı + komisyon payı
    proceeds: Decimal  # satış tutarı − komisyon payı


def realized_lots(transactions: Sequence[Transaction]) -> list[RealizedLot]:
    """Satışları FIFO ile alışlara eşler. Bedelsiz adet, mevcut lotlara orantılı dağıtılır (maliyetsiz)."""
    lots: list[list] = []  # [alış tarihi, adet, maliyet]
    out: list[RealizedLot] = []
    for t in sorted(transactions, key=lambda t: (t.trade_date, t.id or 0)):
        if t.tx_type is TxType.SELL:
            remaining = t.quantity
            net_unit = (t.gross_amount - t.fee) / t.quantity
            while remaining > 0 and lots:
                lot = lots[0]
                take = min(lot[1], remaining)
                cost = lot[2] * take / lot[1]
                out.append(RealizedLot(lot[0], t.trade_date, take, cost, net_unit * take))
                lot[1] -= take
                lot[2] -= cost
                remaining -= take
                if lot[1] <= 0:
                    lots.pop(0)
        elif t.tx_type is TxType.BONUS:
            held = sum((lot[1] for lot in lots), ZERO)
            if held > 0:
                factor = (held + t.quantity) / held
                for lot in lots:
                    lot[1] *= factor
        else:
            lots.append([t.trade_date, t.quantity, t.gross_amount + t.fee])
    return out


def _prev_month(d: date) -> str:
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return f"{y:04d}-{m:02d}"


def indexation_factor(ppi: dict[str, Decimal], buy: date, sell: date) -> tuple[Decimal, bool]:
    """(çarpan, endeks verisi var mı). Artış %10'un altındaysa çarpan 1'dir."""
    a, b = ppi.get(_prev_month(buy)), ppi.get(_prev_month(sell))
    if a is None or b is None or a <= 0:
        return ONE, False
    factor = b / a
    return (factor if factor - ONE >= INDEX_THRESHOLD else ONE), True


@dataclass(frozen=True, slots=True)
class TaxableGain:
    buy_date: date
    sell_date: date
    quantity: Decimal
    cost_try: Decimal
    indexed_cost_try: Decimal
    proceeds_try: Decimal
    indexed: bool  # endeksleme uygulandı mı
    ppi_missing: bool

    @property
    def gain_try(self) -> Decimal:
        return self.proceeds_try - self.indexed_cost_try


def taxable_gains(
    lots: Sequence[RealizedLot],
    fx_on: dict[date, Decimal] | None,
    ppi: dict[str, Decimal],
) -> list[TaxableGain]:
    """Satılan lotların TL kazancı. `fx_on` tarih → kur (TL varlık için None)."""
    out: list[TaxableGain] = []
    for lot in lots:
        if fx_on is None:
            buy_fx = sell_fx = ONE
        else:
            buy_fx, sell_fx = fx_on.get(lot.buy_date), fx_on.get(lot.sell_date)
            if buy_fx is None or sell_fx is None:
                continue
        cost = lot.cost * buy_fx
        factor, has_ppi = indexation_factor(ppi, lot.buy_date, lot.sell_date)
        out.append(
            TaxableGain(
                lot.buy_date,
                lot.sell_date,
                lot.quantity,
                cost,
                cost * factor,
                lot.proceeds * sell_fx,
                factor > ONE,
                not has_ppi,
            )
        )
    return out
