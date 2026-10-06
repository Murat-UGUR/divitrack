"""Para ve miktar yardımcıları. Tüm hesaplar float yerine Decimal ile yapılır."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from divitrack.core.exceptions import ValidationError

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


def to_decimal(value: object, *, field: str = "değer") -> Decimal:
    """str/int/float/Decimal → Decimal. Türkçe ondalık virgülünü de kabul eder."""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        raise ValidationError(f"{field} boş olamaz")
    if isinstance(value, float):
        value = repr(value)
    text = str(value).strip().replace(" ", "")
    if "," in text and "." in text:  # 1.234,56 → 1234.56
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValidationError(f"{field} sayı olmalı: {value!r}") from exc
    if not result.is_finite():
        raise ValidationError(f"{field} sonlu bir sayı olmalı")
    return result


def quantize(value: Decimal, places: int = 2) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def pct(part: Decimal, whole: Decimal) -> Decimal | None:
    """Yüzde oranı; payda 0 ise None."""
    if whole == 0:
        return None
    return part / whole * HUNDRED
