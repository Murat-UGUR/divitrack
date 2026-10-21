from datetime import date, timedelta
from decimal import Decimal as D

from divitrack.core.entities import Asset, CashFlow, Transaction
from divitrack.core.enums import AssetClass, Currency, Purpose, TxType
from divitrack.ui.formatting import fmt_money, fmt_pct, fmt_qty
from divitrack.ui.tables import (
    allocation_frame,
    budget_history_frame,
    budget_lines_frame,
    cpi_frame,
    events_frame,
    flows_frame,
    monthly_income,
    plan_frame,
    positions_frame,
    purpose_frame,
    real_returns_frame,
)


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


def test_analysis_and_budget_frames(container) -> None:
    c = container
    a = c.portfolio.add_asset(Asset("AAPL", AssetClass.US_STOCK, Currency.USD, purpose=Purpose.DIVIDEND))
    c.portfolio.add_transaction(
        Transaction(asset_id=a.id, tx_type=TxType.BUY, quantity=D("1"), price=D("100"), trade_date=date(2024, 1, 1))
    )
    s = c.portfolio.summary(Currency.TRY)
    assert positions_frame(s)["Amaç"].tolist() == ["Temettü geliri"]
    pf = purpose_frame(c.insights.purpose_allocation(s))
    assert pf.loc[pf["Amaç"] == "Temettü geliri", "Pay %"].iloc[0] == 100
    assert round(plan_frame(c.insights.monthly_plan(D("1000"), s))["Yatırılacak"].sum(), 6) == 1000
    rr = real_returns_frame(c.insights.real_returns())
    assert rr["Kur"].tolist() == ["≈ güncel"] and rr["Reel %"].isna().all()

    salary = c.budget.categories.find("Maaş")
    c.budget.add_flow(CashFlow(salary.id, date.today(), D("100")))
    month = c.budget.month_summary(date.today())
    assert budget_lines_frame(month)["Gerçekleşen"].tolist() == [100.0]
    assert budget_history_frame(c.budget.history(2))["Gelir"].tolist() == [0.0, 100.0]
    cats = {x.id: x for x in c.budget.list_categories()}
    assert flows_frame(c.budget.flows_in_month(date.today()), cats)["Kategori"].tolist() == ["Maaş"]
    cpi = cpi_frame({"2026-01": D("100"), "2026-02": D("103")})
    assert cpi["Ay"].tolist() == ["2026-02", "2026-01"]
    assert round(cpi["Aylık %"].iloc[0], 2) == 3.0
    assert budget_lines_frame(c.budget.month_summary(date(2000, 1, 1))).empty
