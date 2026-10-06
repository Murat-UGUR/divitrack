from datetime import date, timedelta
from decimal import Decimal as D

from divitrack.core.entities import Asset, Transaction
from divitrack.core.enums import AssetClass, Currency, TxType
from divitrack.ui.formatting import fmt_money, fmt_pct, fmt_qty
from divitrack.ui.tables import allocation_frame, events_frame, monthly_income, positions_frame


def test_turkish_formatting() -> None:
    assert fmt_money(D("1234567.891"), Currency.TRY) == "₺1.234.567,89"
    assert fmt_money(D("-5"), Currency.USD) == "-$5,00"
    assert fmt_pct(D("-1.5"), signed=True) == "-%1,50"
    assert fmt_pct(D("2"), signed=True) == "+%2,00"
    assert fmt_qty(D("1500.250")) == "1.500,25"


def test_frames(container, provider) -> None:
    c = container
    a = c.portfolio.add_asset(Asset("AAPL", AssetClass.US_STOCK, Currency.USD))
    t = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    for asset, q in ((a, "10"), (t, "5")):
        c.portfolio.add_transaction(
            Transaction(
                asset_id=asset.id, tx_type=TxType.BUY, quantity=D(q), price=D("100"), trade_date=date(2024, 1, 1)
            )
        )
    s = c.portfolio.summary(Currency.TRY)
    df = positions_frame(s)
    assert list(df["Sembol"]) == ["AAPL", "THYAO"]
    assert round(df["Ağırlık %"].sum()) == 100
    alloc = allocation_frame(s)
    assert list(alloc["Etiket"]) == ["BIST Hisse", "ABD Hisse/ETF"]  # sabit sıra

    today = date.today()
    provider.dividends["AAPL"] = [(today - timedelta(days=100), D("1"))]
    events = c.dividends.events(c.portfolio.positions())
    ev = events_frame(events, c.market.fx_rates({Currency.USD}, Currency.TRY), Currency.TRY)
    monthly = monthly_income(ev, Currency.TRY, date(today.year, today.month, 1), months=12)
    assert len(monthly) == 12
    assert monthly["Tahmini"].sum() == 10 * 40 * 0.8
