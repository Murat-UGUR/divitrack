from datetime import datetime, timedelta
from decimal import Decimal as D

from divitrack.core.entities import Asset
from divitrack.core.enums import AssetClass, Currency
from divitrack.services.market.symbols import provider_symbol


def test_provider_symbols() -> None:
    assert provider_symbol(Asset("thyao", AssetClass.BIST_STOCK, Currency.TRY)) == "THYAO.IS"
    assert provider_symbol(Asset("XAU-OZ", AssetClass.PRECIOUS_METAL, Currency.USD)) == "GC=F"
    assert provider_symbol(Asset("XAU-GRAM", AssetClass.PRECIOUS_METAL, Currency.TRY)) is None
    assert provider_symbol(Asset("NAKIT", AssetClass.CASH, Currency.TRY)) is None
    assert provider_symbol(Asset("AAPL", AssetClass.US_STOCK, Currency.USD, manual_price=D(1))) is None


def test_gram_gold_derived_from_ounce_and_usdtry(container) -> None:
    gold = container.portfolio.add_asset(Asset("XAU-GRAM", AssetClass.PRECIOUS_METAL, Currency.USD))
    assert gold.currency is Currency.TRY and gold.name == "Gram Altın"
    q = container.market.quotes_for([gold])[gold.id]
    assert q.source == "derived"
    assert abs(q.price - D("2400") * 40 / D("31.1034768")) < D("0.0001")


def test_cache_used_within_ttl_and_stale_fallback(container, provider) -> None:
    a = container.portfolio.add_asset(Asset("AAPL", AssetClass.US_STOCK, Currency.USD))
    container.market.quotes_for([a])
    container.market.quotes_for([a])
    assert provider.calls == 1  # ikinci çağrı önbellekten

    provider.fail = True
    container.market.clock = lambda: datetime.now() + timedelta(hours=1)  # TTL doldu
    q = container.market.quotes_for([a])[a.id]
    assert q.price == D("200") and q.stale
    assert container.market.last_error


def test_fx_rates(container) -> None:
    rates = container.market.fx_rates({Currency.USD, Currency.EUR, Currency.TRY}, Currency.TRY)
    assert rates == {Currency.TRY: 1, Currency.USD: D(40), Currency.EUR: D(45)}
