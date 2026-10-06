"""Türkçe sayı/para biçimlendirme (UI'dan bağımsız, saf fonksiyonlar)."""

from __future__ import annotations

from decimal import Decimal

from divitrack.core.enums import Currency


def _tr(number: str) -> str:
    return number.replace(",", "_").replace(".", ",").replace("_", ".")


def fmt_number(value: Decimal | float | None, places: int = 2) -> str:
    if value is None:
        return "—"
    return _tr(f"{float(value):,.{places}f}")


def fmt_money(value: Decimal | float | None, currency: Currency, places: int = 2) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}{currency.symbol}{fmt_number(abs(value), places)}"


def fmt_signed_money(value: Decimal | float | None, currency: Currency) -> str:
    if value is None:
        return "—"
    return ("+" if value > 0 else "") + fmt_money(value, currency)


def fmt_pct(value: Decimal | float | None, places: int = 2, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}%{fmt_number(abs(value), places)}"


def fmt_qty(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        whole, frac = text.split(".")
        return f"{_tr(f'{int(whole):,}')},{frac}"
    return _tr(f"{int(text):,}")
