from datetime import date, datetime, timedelta
from decimal import Decimal as D

import pytest

from divitrack.core.entities import Asset, PriceQuote, Transaction
from divitrack.core.enums import AssetClass, Currency, Purpose, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.services.analytics_service import PRESETS, Benchmark, price_freshness
from divitrack.services.insights_service import PlanRules

T0 = date(2024, 1, 2)


def add_tx(c, asset, t, qty, price, d=T0):
    return c.portfolio.add_transaction(
        Transaction(asset_id=asset.id, tx_type=t, quantity=D(qty), price=D(price), trade_date=d)
    )


@pytest.fixture
def setup(container, provider):
    c = container
    provider.history["USDTRY=X"] = [(date(2024, 1, 1), D("30"))]  # işlem günü kuru 30, bugün 40
    c.cpi.replace_all({"2024-01": D("100"), "2025-01": D("150"), "2026-01": D("200")})
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY, purpose=Purpose.GROWTH))
    aapl = c.portfolio.add_asset(Asset("AAPL", AssetClass.US_STOCK, Currency.USD, purpose=Purpose.DIVIDEND))
    add_tx(c, thy, TxType.BUY, "100", "250")  # 25.000 TL → bugün 30.000 TL
    add_tx(c, aapl, TxType.BUY, "10", "150")  # 1.500 $ × 30 = 45.000 TL → bugün 10×200×40 = 80.000 TL
    return c, thy, aapl


# --------------------------------------------------------------------------- alım alım getiri
def test_lot_returns_nominal_real_and_usd(setup) -> None:
    c, thy, aapl = setup
    report = c.analytics.lots()
    by_symbol = {lot.asset.symbol: lot for lot in report.lots}
    t = by_symbol["THYAO"]
    assert t.cost == D("25000")
    assert t.value == D("30000")
    assert t.nominal_pct == D("20")
    assert t.cost_real == D("50000")  # TÜFE 2 katına çıktı
    assert t.real_pct == D("-40")
    assert t.cost_usd is not None and abs(t.cost_usd - D("833.333")) < D("0.01")
    assert t.value_usd == D("750")  # 30.000 / 40
    assert t.usd_pct is not None and abs(t.usd_pct - D("-10")) < D("0.01")

    a = by_symbol["AAPL"]
    assert a.cost == D("45000")
    assert a.cost_usd == D("1500")
    assert a.value_usd == D("2000")
    assert a.usd_pct is not None and abs(a.usd_pct - D("33.333")) < D("0.01")
    assert not a.approx_fx
    assert report.usd_rate == D("40")


def test_lots_follow_sales_and_aggregate_by_asset(setup) -> None:
    c, thy, _ = setup
    add_tx(c, thy, TxType.BUY, "100", "280", date(2024, 6, 3))
    add_tx(c, thy, TxType.SELL, "150", "300", date(2025, 1, 2))  # ilk lotun tamamı + ikincinin yarısı
    report = c.analytics.lots()
    thy_lots = [lot for lot in report.lots if lot.asset.symbol == "THYAO"]
    assert len(thy_lots) == 1
    assert thy_lots[0].trade_date == date(2024, 6, 3)
    assert thy_lots[0].quantity == D("50")
    assert thy_lots[0].cost == D("14000")
    totals = {r.asset.symbol: r for r in report.by_asset()}
    assert totals["THYAO"].value == D("15000")
    total = report.total()
    assert total is not None and total.cost == D("14000") + D("45000")


def test_lots_without_fx_history_fall_back_to_current_rate(container) -> None:
    c = container
    aapl = c.portfolio.add_asset(Asset("AAPL", AssetClass.US_STOCK, Currency.USD))
    add_tx(c, aapl, TxType.BUY, "1", "100")
    lot = c.analytics.lots().lots[0]
    assert lot.approx_fx
    assert lot.cost == D("4000")
    assert lot.cost_real is None  # TÜFE yok


# --------------------------------------------------------------------------- para akışı
def test_contributions_by_month_and_cumulative(setup) -> None:
    c, thy, _ = setup
    add_tx(c, thy, TxType.SELL, "50", "300", date(2024, 3, 5))  # 15.000 TL çekildi
    rows = c.analytics.contributions()
    first, second, third = rows[0], rows[1], rows[2]
    assert first.month == "2024-01" and first.invested == D("70000") and first.withdrawn == 0
    assert second.month == "2024-02" and second.net == 0 and second.cumulative == D("70000")
    assert third.withdrawn == D("15000") and third.cumulative == D("55000")
    assert rows[-1].month == date.today().strftime("%Y-%m")
    assert rows[-1].cumulative == D("55000")
    assert rows[-1].cumulative_real is not None and rows[-1].cumulative_real > rows[-1].cumulative


def test_contributions_empty_without_transactions(container) -> None:
    assert container.analytics.contributions() == []


# --------------------------------------------------------------------------- yıllık getiri ve kıyas
def test_annual_return_and_cpi(setup) -> None:
    c, _, _ = setup
    annual = c.analytics.annual_return()
    assert annual.since == T0
    assert annual.nominal_pct is not None and annual.nominal_pct > 0
    assert annual.cpi_12m_pct is not None and abs(annual.cpi_12m_pct - D("33.333")) < D("0.01")
    assert annual.real_pct is not None and annual.real_pct < annual.nominal_pct


def test_annual_return_none_for_very_new_portfolio(container) -> None:
    c = container
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    add_tx(c, thy, TxType.BUY, "1", "250", date.today() - timedelta(days=5))
    assert c.analytics.annual_return().nominal_pct is None


def test_benchmarks_round_trip_and_table(setup) -> None:
    c, _, _ = setup
    c.analytics.save_benchmarks([Benchmark("Mevduat", D("45")), Benchmark("BIST 100", D("20"))])
    assert [b.name for b in c.analytics.benchmarks()] == ["Mevduat", "BIST 100"]
    rows = c.analytics.benchmark_table()
    assert rows[0].is_portfolio
    deposit = next(r for r in rows if r.name == "Mevduat")
    # (1,45 / 1,3333) − 1 ≈ %8,75
    assert deposit.real_pct is not None and abs(deposit.real_pct - D("8.75")) < D("0.01")


@pytest.mark.parametrize(
    "items",
    [
        [Benchmark(" ", D("10"))],
        [Benchmark("Altın", D("10")), Benchmark("altın", D("12"))],
        [Benchmark("Kripto", D("-100"))],
    ],
)
def test_benchmarks_validation(container, items) -> None:
    with pytest.raises(ValidationError):
        container.analytics.save_benchmarks(items)


def test_corrupt_benchmark_setting_is_ignored(container) -> None:
    container.settings_repo.set("benchmarks", "{bozuk")
    assert container.analytics.benchmarks() == []


# --------------------------------------------------------------------------- senaryo
def test_scenario_by_purpose_and_presets(setup) -> None:
    c, _, _ = setup
    summary = c.portfolio.summary(Currency.TRY)
    preset = next(p for p in PRESETS if p.key == "devaluation")
    rows = {r.purpose: r for r in c.analytics.scenario_by_purpose(summary, preset.class_shocks, preset.fx_shock)}
    assert rows[Purpose.GROWTH].change == 0  # TL hisse, kurdan etkilenmez
    assert rows[Purpose.DIVIDEND].after == D("104000")  # 80.000 × 1,30
    total_after = c.insights.scenario(summary, preset.class_shocks, preset.fx_shock)
    assert sum(r.after for r in rows.values()) == total_after
    assert {p.key for p in PRESETS} >= {"devaluation", "gold_correction", "bist_crash", "us_crash", "bad_day"}


# --------------------------------------------------------------------------- alım öncesi kontrol
def test_purchase_check_flags_single_position_limit(setup) -> None:
    c, _, aapl = setup
    summary = c.portfolio.summary(Currency.TRY)
    rules = PlanRules(single_position_max=D("0.5"))
    check = c.analytics.purchase_check(summary, aapl, D("10000"), rules)
    assert check.verdict == "Kurallara aykırı"
    assert any(i.level == "error" and "tek kalem" in i.text for i in check.items)
    assert check.weight_after > check.weight_before


def test_purchase_check_rewards_underweight_purpose(setup) -> None:
    c, thy, _ = setup
    summary = c.portfolio.summary(Currency.TRY)
    rules = PlanRules(
        targets={
            Purpose.DEFENSIVE: D("0"),
            Purpose.GROWTH: D("0.6"),
            Purpose.DIVIDEND: D("0.4"),
            Purpose.EXPERIMENT: D("0"),
        },
        single_position_max=D("0.9"),
    )
    check = c.analytics.purchase_check(summary, thy, D("10000"), rules)
    assert check.verdict == "Uygun"
    assert any("hedefin altındaydı" in i.text for i in check.items)


def test_purchase_check_rejects_non_positive_amount(setup) -> None:
    c, thy, _ = setup
    with pytest.raises(ValidationError):
        c.analytics.purchase_check(c.portfolio.summary(Currency.TRY), thy, D("0"))


# --------------------------------------------------------------------------- fiyat tazeliği
def test_price_freshness_labels() -> None:
    now = datetime(2026, 11, 1, 12, 0)
    fresh = PriceQuote(D("1"), None, Currency.TRY, now)
    cached = PriceQuote(D("1"), None, Currency.TRY, now, stale=True)
    old = PriceQuote(D("1"), None, Currency.TRY, now - timedelta(days=10), stale=True)
    manual = PriceQuote(D("1"), None, Currency.TRY, None, source="manual")
    today = date(2026, 11, 1)
    assert price_freshness(fresh, today) == "Güncel"
    assert price_freshness(cached, today) == "Önbellek"
    assert price_freshness(old, today) == "Eski (22.10)"
    assert price_freshness(manual, today) == "Manuel"
    assert price_freshness(None, today) == "Fiyat yok"
