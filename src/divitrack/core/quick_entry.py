"""Tek satırlık metinden işlem çıkarma: "5 gr altın 5750", "sat 100 EREGL 45,5", "12 SCHD 31.5$ 18.11.2026".

Kurallar (sıra serbest):
- Fiil isteğe bağlı: al / aldım / alış (varsayılan) · sat / sattım / satış
- İlk sayı adet, ikinci sayı birim fiyat. Birim yazılabilir: gr, gram, g, adet, lot
- Fiyatın yanında para birimi olabilir: tl, ₺, $, usd, €, eur; "5750'den", "@5750" de olur
- Tarih: 20.11.2026, 20/11/2026, 2026-11-20, "bugün", "dün" (yoksa bugün)
- Varlık: sembol (THYAO, AAPL) ya da takma ad: altın → XAU-GRAM, gümüş → XAG-GRAM
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from divitrack.core.enums import Currency, TxType
from divitrack.core.exceptions import ValidationError

ALIASES = {
    "altın": "XAU-GRAM",
    "altin": "XAU-GRAM",
    "gümüş": "XAG-GRAM",
    "gumus": "XAG-GRAM",
    "ons": "XAU-OZ",
}
SELL_WORDS = {"sat", "sattım", "sattim", "satış", "satis", "satildi", "satıldı"}
BUY_WORDS = {"al", "aldım", "aldim", "alış", "alis", "alındı", "alindi"}
UNIT_WORDS = {"gr", "g", "gram", "adet", "lot", "pay", "hisse"}
SKIP_WORDS = {"x", "@", "fiyat", "fiyatından", "fiyatindan", "den", "dan", "ten", "tan"}
CURRENCY_WORDS = {
    "tl": Currency.TRY,
    "₺": Currency.TRY,
    "try": Currency.TRY,
    "$": Currency.USD,
    "usd": Currency.USD,
    "dolar": Currency.USD,
    "€": Currency.EUR,
    "eur": Currency.EUR,
    "euro": Currency.EUR,
}

_NUM = re.compile(r"^[@]?(\d+(?:[.,]\d+)*)([a-z₺$€]*)(?:'?(?:den|dan|ten|tan))?$")
_DATE = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$|^(\d{4})-(\d{2})-(\d{2})$")


@dataclass(frozen=True, slots=True)
class ParsedEntry:
    tx_type: TxType
    quantity: Decimal
    symbol: str
    price: Decimal
    trade_date: date
    currency: Currency | None = None


def _number(text: str) -> Decimal:
    """'1.234,56', '1234.56', '3,25' → Decimal."""
    s = text
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise ValidationError(f"Sayı okunamadı: {text}") from exc


def parse_entry(text: str, today: date) -> ParsedEntry:
    tokens = [t for t in re.split(r"\s+", text.strip().lower().replace("’", "'")) if t]
    if not tokens:
        raise ValidationError("Boş metin")
    tx_type = TxType.BUY
    numbers: list[Decimal] = []
    currency: Currency | None = None
    trade_date = today
    words: list[str] = []
    for raw in tokens:
        tok = raw.strip(",;")
        if tok in SELL_WORDS:
            tx_type = TxType.SELL
            continue
        if tok in BUY_WORDS or tok in UNIT_WORDS or tok in SKIP_WORDS:
            continue
        if tok in ("bugün", "bugun"):
            trade_date = today
            continue
        if tok == "dün" or tok == "dun":
            trade_date = today - timedelta(days=1)
            continue
        bare = re.sub(r"'?(den|dan|ten|tan)$", "", tok)
        if bare in CURRENCY_WORDS:
            currency = CURRENCY_WORDS[bare]
            continue
        if tok.startswith("$") and len(tok) > 1:
            currency, tok = Currency.USD, tok[1:]
        m = _DATE.match(tok)
        if m:
            g = m.groups()
            try:
                trade_date = date(int(g[2]), int(g[1]), int(g[0])) if g[0] else date(int(g[3]), int(g[4]), int(g[5]))
            except ValueError as exc:
                raise ValidationError(f"Geçersiz tarih: {tok}") from exc
            continue
        m = _NUM.match(tok)
        if m:
            numbers.append(_number(m.group(1)))
            suffix = m.group(2).removesuffix("'")
            if suffix in UNIT_WORDS or suffix == "":
                pass
            elif suffix.rstrip("'") in CURRENCY_WORDS:
                currency = CURRENCY_WORDS[suffix.rstrip("'")]
            elif suffix.startswith("tl"):
                currency = Currency.TRY
            continue
        words.append(tok)
    if len(numbers) < 2:
        raise ValidationError("Adet ve fiyat bulunamadı. Örnek: 5 gr altın 5750")
    if len(numbers) > 2:
        raise ValidationError("Fazla sayı var; tek işlem yaz (adet, varlık, fiyat, isteğe bağlı tarih)")
    if not words:
        raise ValidationError("Varlık bulunamadı. Örnek: 40 THYAO 310")
    name = " ".join(words)
    symbol = ALIASES.get(name) or ALIASES.get(words[-1]) or ALIASES.get(words[0]) or words[0].upper()
    if trade_date > today:
        raise ValidationError("İşlem tarihi ileride olamaz")
    quantity, price = numbers
    if quantity <= 0 or price <= 0:
        raise ValidationError("Adet ve fiyat sıfırdan büyük olmalı")
    return ParsedEntry(tx_type, quantity, symbol, price, trade_date, currency)
