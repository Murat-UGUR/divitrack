"""TEFAS (Türkiye Elektronik Fon Alım Satım Platformu) fon fiyatı adaptörü.

2026'da eski `BindHistoryInfo` uç noktası kaldırıldı; yeni JSON API fon başına çalışır ve geçmişi
"kaç ay geriye" (`periyod`: 1, 3, 6, 12, 36, 60) parametresiyle verir:

    POST https://www.tefas.gov.tr/api/funds/fonFiyatBilgiGetir
    {"fonKodu": "TTE", "dil": "TR", "periyod": 1}
    → {"resultList": [{"tarih": "2026-10-20", "fonKodu": "TTE", "fiyat": 1.234567, ...}, ...]}

Resmî olarak belgelenmiş bir API değildir; biçim değişebilir. Bu yüzden servis katmanı her hatada
önbelleğe veya manuel fiyata düşer. Sağlayıcı sembolleri `TEFAS:<KOD>` biçimindedir.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from divitrack.core.entities import ProviderQuote
from divitrack.services.market.http import Transport, post_json, urllib_transport

log = logging.getLogger(__name__)

PREFIX = "TEFAS:"
URL = "https://www.tefas.gov.tr/api/funds/fonFiyatBilgiGetir"
PERIODS = (1, 3, 6, 12, 36, 60)


def tefas_symbol(code: str) -> str:
    return f"{PREFIX}{code.strip().upper()}"


def months_back(start: date, today: date) -> int:
    """`start` tarihine ulaşmak için gereken en küçük geçerli periyot (en fazla 60 ay)."""
    needed = max(1, math.ceil((today - start).days / 30))
    return next((p for p in PERIODS if p >= needed), PERIODS[-1])


def _point(row: dict[str, Any]) -> tuple[date, Decimal] | None:
    raw_date, raw_price = row.get("tarih"), row.get("fiyat")
    if not raw_date or raw_price in (None, ""):
        return None
    try:
        day = date.fromisoformat(str(raw_date)[:10])
        price = Decimal(str(raw_price).replace(",", "."))
    except (ValueError, InvalidOperation):
        return None
    return (day, price) if price > 0 else None


class TefasProvider:
    name = "tefas"

    def __init__(self, transport: Transport | None = None, today: Any = date.today) -> None:
        self.transport = transport or urllib_transport()
        self.today = today

    def _series(self, code: str, period: int) -> list[tuple[date, Decimal]]:
        body = post_json(self.transport, URL, {"fonKodu": code, "dil": "TR", "periyod": period})
        rows = body.get("resultList") if isinstance(body, dict) else None
        points = [p for p in (_point(r) for r in rows or [] if isinstance(r, dict)) if p is not None]
        return sorted(dict(points).items())

    def _codes(self, symbols: Sequence[str]) -> list[tuple[str, str]]:
        return [(s, s.removeprefix(PREFIX)) for s in symbols if s.startswith(PREFIX)]

    def fetch_quotes(self, symbols: Sequence[str]) -> dict[str, ProviderQuote]:
        now = datetime.now()
        out: dict[str, ProviderQuote] = {}
        for symbol, code in self._codes(symbols):
            try:
                series = self._series(code, 1)
            except Exception as exc:  # tek fonun hatası diğerlerini durdurmasın
                log.warning("TEFAS fiyatı alınamadı (%s): %s", code, exc)
                continue
            if not series:
                continue
            prev = series[-2][1] if len(series) > 1 else None
            out[symbol] = ProviderQuote(symbol=symbol, price=series[-1][1], previous_close=prev, fetched_at=now)
        return out

    def fetch_dividends(self, symbol: str) -> list[tuple[date, Decimal]]:
        return []  # fonlar temettü dağıtmaz; getiri fiyata yansır

    def fetch_history(self, symbols: Sequence[str], start: date) -> dict[str, list[tuple[date, Decimal]]]:
        period = months_back(start, self.today())
        out: dict[str, list[tuple[date, Decimal]]] = {}
        for symbol, code in self._codes(symbols):
            try:
                out[symbol] = [(d, p) for d, p in self._series(code, period) if d >= start]
            except Exception as exc:
                log.warning("TEFAS geçmişi alınamadı (%s): %s", code, exc)
        return out


class CompositeProvider:
    """Sembol önekine göre doğru sağlayıcıya yönlendirir (ör. `TEFAS:` → TefasProvider, diğerleri Yahoo)."""

    name = "composite"

    def __init__(self, routes: dict[str, Any], default: Any | None) -> None:
        self.routes = routes
        self.default = default

    def _split(self, symbols: Sequence[str]) -> list[tuple[Any, list[str]]]:
        groups: dict[int, tuple[Any, list[str]]] = {}
        for s in symbols:
            provider = next((p for prefix, p in self.routes.items() if s.startswith(prefix)), self.default)
            if provider is None:
                continue
            groups.setdefault(id(provider), (provider, []))[1].append(s)
        return list(groups.values())

    def fetch_quotes(self, symbols: Sequence[str]) -> dict[str, ProviderQuote]:
        out: dict[str, ProviderQuote] = {}
        errors: list[Exception] = []
        for provider, group in self._split(symbols):
            try:
                out.update(provider.fetch_quotes(group))
            except Exception as exc:
                errors.append(exc)
        if errors and not out:
            raise errors[0]
        return out

    def fetch_dividends(self, symbol: str) -> list[tuple[date, Decimal]]:
        groups = self._split([symbol])
        return groups[0][0].fetch_dividends(symbol) if groups else []

    def fetch_history(self, symbols: Sequence[str], start: date) -> dict[str, list[tuple[date, Decimal]]]:
        out: dict[str, list[tuple[date, Decimal]]] = {}
        for provider, group in self._split(symbols):
            try:
                out.update(provider.fetch_history(group, start))
            except Exception as exc:
                log.warning("Geçmiş alınamadı (%s): %s", provider.name, exc)
        return out
