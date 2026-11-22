"""0.8 servisleri: mevduat faizi, varlık bazında kayıt ve "ne değişti", risk, vergi ön izleme, planlar."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from divitrack.core.entities import Alert, Asset, Transaction
from divitrack.core.enums import AlertKind, AssetClass, Currency, Purpose, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.core.gold_plan import BuyPlan

TODAY = date.today()


def add_tx(c, asset, t, qty, price, d, fee="0"):
    return c.portfolio.add_transaction(
        Transaction(asset_id=asset.id, tx_type=t, quantity=D(qty), price=D(price), trade_date=d, fee=D(fee))
    )


# --------------------------------------------------------------------------- mevduat faizi
def test_deposit_interest_accrues_daily(container) -> None:
    c = container
    dep = c.portfolio.add_asset(Asset("MEVDUAT", AssetClass.CASH, Currency.TRY, annual_rate=D("40")))
    add_tx(c, dep, TxType.BUY, "100000", "1", TODAY - timedelta(days=365))
    pv = next(p for p in c.portfolio.summary(Currency.TRY).open_positions if p.asset.symbol == "MEVDUAT")
    assert pv.quote is not None and pv.quote.source == "interest"
    assert abs(pv.market_value_base - D("140000")) < D("1")
    assert pv.day_change_base is not None and pv.day_change_base > 0


def test_interest_only_for_cash(container) -> None:
    with pytest.raises(ValidationError):
        Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY, annual_rate=D("40"))
    with pytest.raises(ValidationError):
        Asset("MEV", AssetClass.CASH, Currency.TRY, annual_rate=D("-1"))


# --------------------------------------------------------------------------- ne değişti?
def test_asset_snapshots_and_changes(container, provider) -> None:
    c = container
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    add_tx(c, thy, TxType.BUY, "100", "250", date(2026, 1, 5))
    d1, d2 = date(2026, 11, 1), date(2026, 11, 20)
    c.snapshots.today = lambda: d1
    c.snapshots.record(c.portfolio.summary(Currency.TRY))  # 100 × 300
    add_tx(c, thy, TxType.BUY, "50", "280", date(2026, 11, 10))
    provider.prices["THYAO.IS"] = ("320", "310")
    c.market.clear_cache()
    c.snapshots.today = lambda: d2
    c.snapshots.record(c.portfolio.summary(Currency.TRY))  # 150 × 320
    assert c.snapshots.snapshot_dates(Currency.TRY) == [d1, d2]
    rep = c.snapshots.changes(Currency.TRY, d1)
    assert rep is not None and rep.until == d2
    (row,) = rep.rows
    assert row.value_before == D("30000") and row.value_after == D("48000")
    assert row.price_effect == D("2000")  # 100 × (320 − 300)
    assert row.flow_effect == D("16000")  # 50 yeni adet × 320
    assert row.cost_added == D("14000")
    assert rep.total_change == D("18000")
    assert row.price_change_pct is not None and abs(row.price_change_pct - D("6.6667")) < D("0.001")


def test_record_same_day_replaces_rows(container) -> None:
    c = container
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    tx = add_tx(c, thy, TxType.BUY, "10", "250", date(2026, 1, 5))
    c.snapshots.record(c.portfolio.summary(Currency.TRY))
    gold = c.portfolio.add_asset(Asset("XAU-GRAM", AssetClass.PRECIOUS_METAL, Currency.TRY))
    add_tx(c, gold, TxType.BUY, "1", "3000", date(2026, 1, 5))
    c.portfolio.delete_transaction(tx.id)
    c.snapshots.record(c.portfolio.summary(Currency.TRY))
    (day,) = c.snapshots.snapshot_dates(Currency.TRY)
    rows = c.snapshots.asset_snapshots.on(day, Currency.TRY)
    assert [r.asset_id for r in rows] == [gold.id]


# --------------------------------------------------------------------------- risk
def test_risk_report(container, provider) -> None:
    c = container
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    aapl = c.portfolio.add_asset(Asset("AAPL", AssetClass.US_STOCK, Currency.USD))
    add_tx(c, thy, TxType.BUY, "100", "250", TODAY - timedelta(days=200))
    add_tx(c, aapl, TxType.BUY, "10", "150", TODAY - timedelta(days=200))
    days = [TODAY - timedelta(days=i) for i in range(120, 0, -1)]
    provider.history["THYAO.IS"] = [(d, D(str(300 + (5 if i % 2 else -5)))) for i, d in enumerate(days)]
    provider.history["AAPL"] = [(d, D(str(200 + (2 if i % 2 else -2)))) for i, d in enumerate(days)]
    provider.history["USDTRY=X"] = [(days[0], D("40"))]
    from divitrack.core.entities import Snapshot

    snaps = c.snapshots.snapshots
    snaps.upsert(Snapshot(TODAY - timedelta(days=3), D("100000"), D("90000"), Currency.TRY))
    snaps.upsert(Snapshot(TODAY - timedelta(days=2), D("120000"), D("100000"), Currency.TRY))  # +10.000 yeni para
    snaps.upsert(Snapshot(TODAY - timedelta(days=1), D("99000"), D("100000"), Currency.TRY))
    rep = c.risk.report()
    assert rep.drawdown is not None and abs(rep.drawdown.max_pct - D("-17.5")) < D("0.01")
    vols = {r.asset.symbol: r.volatility_pct for r in rep.assets}
    assert vols["THYAO"] is not None and vols["AAPL"] is not None and vols["THYAO"] > vols["AAPL"]
    assert rep.correlation("THYAO", "AAPL") == D("1.0")
    assert rep.portfolio_volatility_pct is not None
    assert abs(sum(r.weight_pct for r in rep.assets) - 100) < D("0.0001")


# --------------------------------------------------------------------------- vergi
def test_foreign_sale_tax_preview(container, provider) -> None:
    c = container
    aapl = c.portfolio.add_asset(Asset("AAPL", AssetClass.US_STOCK, Currency.USD))
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    add_tx(c, aapl, TxType.BUY, "10", "150", date(2025, 1, 10))
    add_tx(c, aapl, TxType.SELL, "4", "200", date(2026, 3, 2))
    add_tx(c, thy, TxType.BUY, "10", "100", date(2025, 1, 10))
    add_tx(c, thy, TxType.SELL, "10", "300", date(2026, 3, 2))  # BIST: kapsam dışı
    provider.history["USDTRY=X"] = [(date(2025, 1, 10), D("35")), (date(2026, 3, 2), D("45"))]
    c.ppi.replace_all({"2024-12": D("100"), "2026-02": D("130")})
    assert c.tax.years_with_sales() == [2026]
    p = c.tax.foreign_sales(2026)
    (row,) = p.rows
    (g,) = row.gains
    assert g.cost_try == D("21000") and g.indexed_cost_try == D("27300") and g.proceeds_try == D("36000")
    assert p.net_gain == D("8700") and p.taxable == D("8700") and not p.missing_fx
    c.tax.set_exemption(D("5000"))
    assert c.tax.foreign_sales(2026).taxable == D("3700")
    assert c.tax.foreign_sales(2025).rows == []


# --------------------------------------------------------------------------- planlar
def test_buy_plan_status(container) -> None:
    c = container
    gold = c.portfolio.add_asset(Asset("XAU-GRAM", AssetClass.PRECIOUS_METAL, Currency.TRY))
    add_tx(c, gold, TxType.BUY, "20", "3000", date(2026, 8, 1))
    add_tx(c, gold, TxType.BUY, "10", "2900", date(2026, 9, 8))
    add_tx(c, gold, TxType.BUY, "4", "3100", date(2026, 10, 3))
    c.plans.today = lambda: date(2026, 10, 20)
    assert c.plans.buy_plan_status() is None
    c.plans.save_buy_plan(BuyPlan("XAU-GRAM", D("10"), "2026-09", dip_price=D("3100"), dip_extra=D("5")))
    plan, asset, st = c.plans.buy_plan_status()
    assert asset.id == gold.id and plan.dip_extra == D("5")
    assert [m.bought for m in st.months] == [D("10"), D("4")]
    assert st.this_month_remaining == D("6") and st.behind == D("6")
    assert st.price_now is not None and abs(st.price_now - D("3086.4675")) < D("0.01")  # 2400 × 40 / 31,1035
    assert st.dip_active and st.suggested == D("11")
    assert st.months[0].average_after == (D("60000") + D("29000")) / D("30")
    c.plans.save_buy_plan(None)
    assert c.plans.buy_plan() is None


def test_buy_plan_missing_asset(container) -> None:
    container.plans.save_buy_plan(BuyPlan("XAG-GRAM", D("10"), "2026-09"))
    with pytest.raises(ValidationError):
        container.plans.buy_plan_status()


def test_passive_income(container, provider) -> None:
    c = container
    schd = c.portfolio.add_asset(Asset("SCHD", AssetClass.US_STOCK, Currency.USD, purpose=Purpose.DIVIDEND))
    add_tx(c, schd, TxType.BUY, "100", "25", date(2025, 1, 2))
    provider.dividends["SCHD"] = [(TODAY - timedelta(days=d), D("0.25")) for d in (30, 120, 210, 300)]
    c.plans.set_passive_target(D("1000"))
    inc = c.plans.passive_income()
    # 4 × 0,25 $ × 100 adet × %80 (ABD stopajı) × 40 TL = 3.200 TL/yıl
    assert abs(inc.next_12m_net - D("3200")) < D("1")
    assert abs(inc.monthly - D("266.67")) < D("0.01")
    assert inc.paying_value == D("108000")  # 100 × 27 × 40
    assert inc.progress_pct is not None and abs(inc.progress_pct - D("26.667")) < D("0.01")
    assert inc.capital_needed is not None and abs(inc.capital_needed - inc.gap_monthly * 12 / (inc.yield_pct / 100)) < 1
    with pytest.raises(ValidationError):
        c.plans.set_passive_target(D("0"))


def test_forward_real_break_even(container, provider) -> None:
    c = container
    provider.history["USDTRY=X"] = [(date(2024, 1, 1), D("30"))]
    c.cpi.replace_all({"2024-01": D("100"), "2026-01": D("200")})
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    add_tx(c, thy, TxType.BUY, "100", "250", date(2024, 1, 2))
    (row,) = c.analytics.break_even()
    assert row.real == D("500") and row.real_12m is None
    c.plans.set_expected_cpi(D("30"))
    (row,) = c.analytics.break_even()
    assert row.real_12m == D("650") and row.real_12m_gap_pct is not None
    assert abs(row.real_12m_gap_pct - (D("650") / D("300") - 1) * 100) < D("0.0001")


# --------------------------------------------------------------------------- yedek
def test_backup_roundtrip_new_fields(container, tmp_path) -> None:
    c = container
    dep = c.portfolio.add_asset(Asset("MEVDUAT", AssetClass.CASH, Currency.TRY, annual_rate=D("42.5")))
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    c.ppi.replace_all({"2026-01": D("123.4")})
    c.transfer.alerts.add(Alert(AlertKind.PRICE_BELOW, D("280"), thy.id, "ucuz"))
    c.transfer.alerts.add(Alert(AlertKind.DRAWDOWN, D("10")))
    payload = c.transfer.export_json()
    data = json.loads(payload)
    assert data["ppi"] == {"2026-01": "123.4"} and len(data["alerts"]) == 2
    c.transfer.import_json(payload, replace=True)
    assets = {a.symbol: a for a in c.portfolio.assets.list()}
    assert assets["MEVDUAT"].annual_rate == D("42.5")
    assert c.ppi.all() == {"2026-01": D("123.4")}
    kinds = sorted((a.kind.value, a.asset_id == assets["THYAO"].id) for a in c.transfer.alerts.list())
    assert kinds == [("DRAWDOWN", False), ("PRICE_BELOW", True)]
    del dep
