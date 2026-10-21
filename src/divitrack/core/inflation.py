"""Enflasyon (TÜFE) hesapları.

TÜFE, ay anahtarı ("YYYY-AA") → endeks değeri sözlüğü olarak tutulur. Endeksin tabanı önemli
değildir (TÜİK 2003=100 veya kendi seçtiğiniz bir ay=100); yalnızca oranlar kullanılır.
Bir ayın değeri yoksa önceki en yakın ay kullanılır, çünkü TÜFE bir sonraki ay açıklanır.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from divitrack.core.exceptions import ValidationError
from divitrack.core.money import HUNDRED, ONE

CpiIndex = Mapping[str, Decimal]


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def parse_month(value: str) -> str:
    """'2026-3', '2026-03', '03.2026' veya '3/2026' → '2026-03'."""
    text = value.strip().replace("/", ".")
    try:
        if "-" in text:
            year, month = text.split("-")[:2]
        else:
            month, year = text.split(".")[:2]
        y, m = int(year), int(month)
    except ValueError as exc:
        raise ValidationError(f"Ay biçimi anlaşılamadı: {value!r} (ör. 2026-03)") from exc
    if not 1 <= m <= 12 or not 1900 <= y <= 2200:
        raise ValidationError(f"Geçersiz ay: {value!r}")
    return f"{y:04d}-{m:02d}"


def latest_month(index: CpiIndex) -> str | None:
    return max(index) if index else None


def cpi_at(index: CpiIndex, month: str) -> Decimal | None:
    """`month` ayındaki veya öncesindeki son endeks değeri."""
    candidates = [k for k in index if k <= month]
    return index[max(candidates)] if candidates else None


def inflation_factor(index: CpiIndex, start: date, end: date | None = None) -> Decimal | None:
    """`start` ayındaki 1 TL'nin `end` ayındaki karşılığı (ör. 1.45). Veri yoksa None.

    `end` verilmezse eldeki son TÜFE ayı kullanılır.
    """
    if not index:
        return None
    end_key = month_key(end) if end else latest_month(index)
    assert end_key is not None
    base = cpi_at(index, month_key(start))
    current = cpi_at(index, end_key)
    if base is None or current is None or base <= 0:
        return None
    return current / base


def index_from_changes(
    base_month: str, changes: Sequence[tuple[str, Decimal]], base: Decimal = HUNDRED
) -> dict[str, Decimal]:
    """Aylık % değişimlerden zincirleme endeks üretir (base_month = `base`).

    `changes`: [("2026-01", Decimal("4.84")), ...] — her ay bir önceki aya göre % değişim.
    """
    index = {parse_month(base_month): base}
    level = base
    for month, change in sorted((parse_month(m), c) for m, c in changes):
        level = level * (ONE + change / HUNDRED)
        index[month] = level
    return index


def monthly_changes(index: CpiIndex) -> list[tuple[str, Decimal]]:
    """Ardışık iki endeks değeri arasındaki % değişim (tablo gösterimi için)."""
    keys = sorted(index)
    out: list[tuple[str, Decimal]] = []
    for prev, cur in zip(keys, keys[1:], strict=False):
        if index[prev] > 0:
            out.append((cur, (index[cur] / index[prev] - ONE) * HUNDRED))
    return out
