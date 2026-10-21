"""Fiyat ve kur servisi: önbellek + sağlayıcı + türetilmiş fiyatlar + çevrimdışı yedek."""

from __future__ import annotations

import logging
from bisect import bisect_right
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

from divitrack.core.entities import Asset, PriceQuote, ProviderQuote
from divitrack.core.enums import AssetClass, Currency
from divitrack.core.money import ONE
from divitrack.core.ports import PriceProvider
from divitrack.repositories.sqlite import PriceCacheRepository
from divitrack.services.market.symbols import (
    DERIVED_GRAM_METALS,
    fx_symbol,
    ounce_to_gram_try,
    provider_symbol,
    required_symbols,
)

log = logging.getLogger(__name__)

Series = dict[date, Decimal]


def value_on(series: Series, day: date) -> Decimal | None:
    """`day` tarihindeki veya öncesindeki son değer (hafta sonu/tatil boşlukları için)."""
    if not series:
        return None
    keys = sorted(series)
    idx = bisect_right(keys, day)
    return None if idx == 0 else series[keys[idx - 1]]


class MarketDataService:
    def __init__(
        self,
        provider: PriceProvider | None,
        cache: PriceCacheRepository,
        ttl_minutes: int = 15,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.provider = provider
        self.cache = cache
        self.ttl = timedelta(minutes=ttl_minutes)
        self.clock = clock
        self.last_error: str | None = None
        self._history_cache: dict[tuple[tuple[str, ...], date], tuple[datetime, dict[str, Series]]] = {}
        self.history_ttl = timedelta(hours=6)

    @property
    def online(self) -> bool:
        return self.provider is not None

    # ------------------------------------------------------------------ ham fiyatlar
    def _raw_quotes(self, symbols: Iterable[str], *, force: bool = False) -> tuple[dict[str, ProviderQuote], set[str]]:
        """Sembol → ham fiyat. İkinci değer: bayat (önbellekten, güncellenemeyen) semboller."""
        wanted = sorted(set(symbols))
        if not wanted:
            return {}, set()
        cached = self.cache.get_many(wanted)
        now = self.clock()
        fresh = {s: q for s, q in cached.items() if not force and now - q.fetched_at < self.ttl}
        missing = [s for s in wanted if s not in fresh]
        fetched: dict[str, ProviderQuote] = {}
        if missing and self.provider is not None:
            try:
                fetched = self.provider.fetch_quotes(missing)
                self.last_error = None
            except Exception as exc:  # ağ hatası, sağlayıcı değişikliği vb.
                log.warning("Fiyat çekilemedi: %s", exc)
                self.last_error = str(exc)
            if fetched:
                self.cache.upsert_many(fetched.values())
        result = {**fresh, **fetched}
        stale: set[str] = set()
        for s in missing:
            if s not in result and s in cached:
                result[s] = cached[s]
                stale.add(s)
        return result, stale

    # ------------------------------------------------------------------ varlık fiyatları
    def quotes_for(self, assets: Sequence[Asset], *, force: bool = False) -> dict[int, PriceQuote]:
        symbols = [s for a in assets for s in required_symbols(a)]
        raw, stale = self._raw_quotes(symbols, force=force)
        quotes: dict[int, PriceQuote] = {}
        for asset in assets:
            assert asset.id is not None
            q = self._quote_for(asset, raw, stale)
            if q is not None:
                quotes[asset.id] = q
        return quotes

    def _quote_for(self, asset: Asset, raw: dict[str, ProviderQuote], stale: set[str]) -> PriceQuote | None:
        if asset.manual_price is not None:
            return PriceQuote(asset.manual_price, asset.manual_price, asset.currency, None, "manual")
        if asset.asset_class is AssetClass.CASH:
            return PriceQuote(ONE, ONE, asset.currency, None, "cash")
        if asset.asset_class is AssetClass.PRECIOUS_METAL and asset.symbol in DERIVED_GRAM_METALS:
            oz = raw.get(DERIVED_GRAM_METALS[asset.symbol])
            fx = raw.get(fx_symbol(Currency.USD, Currency.TRY))
            if oz is None or fx is None:
                return None
            prev = (
                ounce_to_gram_try(oz.previous_close, fx.previous_close)
                if oz.previous_close is not None and fx.previous_close is not None
                else None
            )
            return PriceQuote(
                ounce_to_gram_try(oz.price, fx.price),
                prev,
                Currency.TRY,
                min(oz.fetched_at, fx.fetched_at),
                "derived",
                stale=bool({oz.symbol, fx.symbol} & stale),
            )
        sym = provider_symbol(asset)
        q = raw.get(sym) if sym else None
        if q is None:
            return None
        return PriceQuote(q.price, q.previous_close, asset.currency, q.fetched_at, "market", sym in stale)

    # ------------------------------------------------------------------ kurlar
    def fx_rates(
        self, currencies: Iterable[Currency], base: Currency, *, force: bool = False
    ) -> dict[Currency, Decimal]:
        """Her para biriminin 1 biriminin `base` cinsinden değeri. Bulunamayanlar sözlükte yer almaz."""
        needed = {c for c in currencies if c is not base}
        # Hem doğrudan (USDTRY=X) hem ters (TRYUSD=X) sembolü iste; hangisi gelirse kullanılır.
        symbols = {fx_symbol(c, base) for c in needed} | {fx_symbol(base, c) for c in needed}
        raw, _ = self._raw_quotes(symbols, force=force)
        rates: dict[Currency, Decimal] = {base: ONE}
        for c in needed:
            direct = raw.get(fx_symbol(c, base))
            inverse = raw.get(fx_symbol(base, c))
            if direct is not None and direct.price > 0:
                rates[c] = direct.price
            elif inverse is not None and inverse.price > 0:
                rates[c] = ONE / inverse.price
        return rates

    def describe(self, asset: Asset) -> str | None:
        """Sağlayıcı destekliyorsa varlığın adını döndürür (ör. TEFAS fon unvanı). Hata olursa None."""
        sym = provider_symbol(asset)
        describe = getattr(self.provider, "describe", None)
        if sym is None or not callable(describe):
            return None
        try:
            name = describe(sym)
        except Exception as exc:
            log.info("Ad bulunamadı (%s): %s", sym, exc)
            return None
        return str(name) if name else None

    def clear_cache(self) -> None:
        self.cache.clear()
        self._history_cache.clear()

    # ------------------------------------------------------------------ geçmiş
    def price_history(self, assets: Sequence[Asset], start: date) -> dict[int, Series]:
        """Varlık id → {tarih: fiyat (varlık para biriminde)}. Manuel/nakit varlıklar sabit."""
        symbols = sorted({s for a in assets for s in required_symbols(a)})
        hist = self._history(symbols, start)
        result: dict[int, Series] = {}
        for a in assets:
            assert a.id is not None
            if a.manual_price is not None or a.asset_class is AssetClass.CASH:
                result[a.id] = {start: a.manual_price if a.manual_price is not None else ONE}
            elif a.asset_class is AssetClass.PRECIOUS_METAL and a.symbol in DERIVED_GRAM_METALS:
                oz = hist.get(DERIVED_GRAM_METALS[a.symbol], {})
                fx = hist.get(fx_symbol(Currency.USD, Currency.TRY), {})
                series: Series = {}
                for d, v in oz.items():
                    rate = value_on(fx, d)
                    if rate is not None:
                        series[d] = ounce_to_gram_try(v, rate)
                result[a.id] = series
            else:
                sym = provider_symbol(a)
                result[a.id] = hist.get(sym, {}) if sym else {}
        return result

    def fx_history(self, currencies: Iterable[Currency], base: Currency, start: date) -> dict[Currency, Series]:
        needed = sorted({c for c in currencies if c is not base})
        hist = self._history([fx_symbol(c, base) for c in needed], start)
        out: dict[Currency, Series] = {base: {start: ONE}}
        for c in needed:
            out[c] = hist.get(fx_symbol(c, base), {})
        return out

    def _history(self, symbols: Sequence[str], start: date) -> dict[str, Series]:
        if not symbols or self.provider is None:
            return {}
        key = (tuple(sorted(symbols)), start)
        hit = self._history_cache.get(key)
        if hit is not None and self.clock() - hit[0] < self.history_ttl:
            return hit[1]
        try:
            raw = self.provider.fetch_history(symbols, start)
        except Exception as exc:
            log.warning("Geçmiş fiyat çekilemedi: %s", exc)
            self.last_error = str(exc)
            return {}
        result = {s: dict(points) for s, points in raw.items()}
        self._history_cache[key] = (self.clock(), result)
        return result
