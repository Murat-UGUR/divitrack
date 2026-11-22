"""Aracı kurum / banka işlem dökümlerinden alış-satış satırları çıkarma (aracı kurum, portföy uygulaması, banka).

Her kurumun kolon adları farklıdır; başlıklar anahtar kelimelerle otomatik eşlenir, kullanıcı düzeltip
eşlemeyi profil olarak saklayabilir. Fiyat kolonu yoksa tutar ÷ adet kullanılır.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from divitrack.core.enums import Currency, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.core.quick_entry import ALIASES, _number
from divitrack.core.statement import parse_date, tr_lower

FIELDS = ("date", "symbol", "side", "quantity", "price", "amount", "fee", "currency")
FIELD_LABELS = {
    "date": "Tarih",
    "symbol": "Sembol / varlık",
    "side": "İşlem tipi (alış/satış)",
    "quantity": "Adet / gram",
    "price": "Birim fiyat",
    "amount": "Tutar",
    "fee": "Komisyon",
    "currency": "Para birimi",
}
KEYWORDS: dict[str, tuple[str, ...]] = {
    "date": ("işlem tarihi", "tarih", "date", "zaman"),
    "symbol": ("sembol", "hisse", "enstrüman", "menkul", "varlık", "fon kodu", "kod", "symbol", "ticker", "ürün"),
    "side": ("işlem tipi", "işlem türü", "yön", "alış/satış", "al/sat", "işlem", "side", "type", "tür"),
    "quantity": ("adet", "miktar", "lot", "gram", "nominal", "quantity", "shares", "pay"),
    "price": ("birim fiyat", "işlem fiyatı", "fiyat", "price", "kur"),
    "amount": ("işlem tutarı", "toplam tutar", "tutar", "toplam", "amount", "total"),
    "fee": ("komisyon", "masraf", "ücret", "fee", "commission"),
    "currency": ("para birimi", "döviz cinsi", "döviz", "currency", "pb"),
}
BUY_WORDS = ("alış", "alis", "al", "buy", "b", "alım", "alim")
SELL_WORDS = ("satış", "satis", "sat", "sell", "s", "satım", "satim")
CURRENCIES = {
    "tl": Currency.TRY,
    "try": Currency.TRY,
    "₺": Currency.TRY,
    "usd": Currency.USD,
    "$": Currency.USD,
    "dolar": Currency.USD,
    "eur": Currency.EUR,
    "€": Currency.EUR,
    "euro": Currency.EUR,
}


def guess_columns(header: Sequence[str]) -> dict[str, int | None]:
    """Başlıkları anahtar kelimelere göre alanlara eşler (en uzun eşleşen kelime kazanır, kolon bir kez kullanılır)."""
    names = [tr_lower(h) for h in header]
    scored: list[tuple[int, str, int]] = []
    for f, words in KEYWORDS.items():
        for i, n in enumerate(names):
            hit = max((len(w) for w in words if w == n or w in n.split() or (len(w) > 3 and w in n)), default=0)
            if hit:
                scored.append((hit, f, i))
    out: dict[str, int | None] = dict.fromkeys(FIELDS)
    used: set[int] = set()
    for _, f, i in sorted(scored, key=lambda x: -x[0]):
        if out[f] is None and i not in used:
            out[f] = i
            used.add(i)
    return out


@dataclass(frozen=True, slots=True)
class BrokerRow:
    line: int
    trade_date: date
    symbol: str
    tx_type: TxType
    quantity: Decimal
    price: Decimal
    fee: Decimal
    currency: Currency | None


@dataclass(slots=True)
class BrokerParse:
    rows: list[BrokerRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: int = 0  # alış/satış olmayan satırlar (temettü, virman…)


def _side(text: str) -> TxType | None:
    t = tr_lower(text).strip()
    words = t.replace("/", " ").split()
    if any(w in SELL_WORDS for w in words) or t.startswith("sat"):
        return TxType.SELL
    if any(w in BUY_WORDS for w in words) or t.startswith("al"):
        return TxType.BUY
    return None


def _symbol(text: str) -> str:
    t = tr_lower(text).strip()
    for word, sym in ALIASES.items():
        if word in t.split() or t == word or t.startswith(word):
            return sym
    head = text.strip().split()[0] if text.strip() else ""
    return head.upper().removesuffix(".IS")


def _num(cell: str) -> Decimal:
    clean = cell.replace("TL", "").replace("₺", "").replace("$", "").replace("USD", "").replace(" ", "").strip()
    return abs(_number(clean.lstrip("+-")))


def parse_broker(rows: Sequence[Sequence[str]], cols: dict[str, int | None], *, first_line: int = 2) -> BrokerParse:
    for need in ("date", "symbol", "quantity"):
        if cols.get(need) is None:
            raise ValidationError(f"{FIELD_LABELS[need]} kolonu seçilmeli")
    if cols.get("price") is None and cols.get("amount") is None:
        raise ValidationError("Birim fiyat veya tutar kolonundan biri seçilmeli")
    out = BrokerParse()

    def cell(r: Sequence[str], f: str) -> str:
        i = cols.get(f)
        return r[i].strip() if i is not None and i < len(r) else ""

    for n, r in enumerate(rows, start=first_line):
        if not any(c.strip() for c in r):
            continue
        try:
            side_text = cell(r, "side")
            tx_type = _side(side_text) if cols.get("side") is not None else TxType.BUY
            if tx_type is None:
                out.skipped += 1
                continue
            qty = _num(cell(r, "quantity"))
            if qty <= 0:
                raise ValidationError("adet sıfır")
            price = _num(cell(r, "price")) if cell(r, "price") else _num(cell(r, "amount")) / qty
            fee = _num(cell(r, "fee")) if cell(r, "fee") else Decimal(0)
            ccy = CURRENCIES.get(tr_lower(cell(r, "currency")).strip()) if cell(r, "currency") else None
            symbol = _symbol(cell(r, "symbol"))
            if not symbol:
                raise ValidationError("sembol boş")
            raw_date = cell(r, "date").split()[0] if cell(r, "date") else ""
            out.rows.append(BrokerRow(n, parse_date(raw_date), symbol, tx_type, qty, price, fee, ccy))
        except (ValidationError, ValueError, ArithmeticError) as exc:
            out.errors.append(f"Satır {n}: {exc}")
    return out
