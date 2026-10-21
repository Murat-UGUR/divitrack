import sqlite3
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from divitrack.core.entities import Asset, CashFlow, Category, Dividend, Transaction
from divitrack.core.enums import AssetClass, Currency, DividendStatus, FlowKind, Purpose, TxType
from divitrack.core.exceptions import DuplicateError, ImportFormatError, ValidationError
from divitrack.db.database import Database
from divitrack.db.schema import MIGRATIONS, SCHEMA_VERSION
from divitrack.services.budget_service import add_months, month_start
from divitrack.services.insights_service import PlanRules

TODAY = date.today()


def add_tx(c, asset, t, qty, price, d=date(2024, 1, 2)):
    return c.portfolio.add_transaction(
        Transaction(asset_id=asset.id, tx_type=t, quantity=D(qty), price=D(price), trade_date=d)
    )


def cat(c, name):
    found = c.budget.categories.find(name)
    assert found is not None
    return found


def flow(c, name, amount, d=TODAY, note=""):
    return c.budget.add_flow(CashFlow(cat(c, name).id, d, D(amount), note))


@pytest.fixture
def portfolio(container):
    c = container
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY, purpose=Purpose.GROWTH))
    aapl = c.portfolio.add_asset(Asset("AAPL", AssetClass.US_STOCK, Currency.USD, purpose=Purpose.DIVIDEND))
    gold = c.portfolio.add_asset(Asset("XAU-GRAM", AssetClass.PRECIOUS_METAL, Currency.TRY, purpose=Purpose.DEFENSIVE))
    fund = c.portfolio.add_asset(Asset("TTE", AssetClass.FUND, Currency.TRY, manual_price=D("2")))
    add_tx(c, thy, TxType.BUY, "100", "250")  # 30.000 TL değer
    add_tx(c, aapl, TxType.BUY, "10", "150")  # 10×200×40 = 80.000 TL
    add_tx(c, gold, TxType.BUY, "10", "2500")  # gram = 2400×40/31.10 ≈ 3086.8 → ~30.868 TL
    add_tx(c, fund, TxType.BUY, "1000", "1")  # 2.000 TL, amaç atanmadı
    return c, thy, aapl, gold, fund


# --------------------------------------------------------------------------- migration
def test_v1_database_upgrades_with_data(tmp_path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0])
    conn.execute("INSERT INTO assets(symbol,name,asset_class,currency) VALUES ('THYAO','','BIST_STOCK','TRY')")
    conn.execute(
        "INSERT INTO transactions(asset_id,tx_type,quantity,price,fee,trade_date) VALUES (1,'BUY','5','10','0','2024-01-01')"
    )
    conn.execute(
        "INSERT INTO dividends(asset_id,pay_date,amount_per_share,quantity,currency,status) "
        "VALUES (1,'2024-06-01','1','5','TRY','PAID')"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    db = Database(path)
    assert db.schema_version() == SCHEMA_VERSION
    with db.transaction() as c:
        assert c.execute("SELECT purpose FROM assets").fetchone()[0] == "UNASSIGNED"
        assert c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM dividends").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM categories").fetchone()[0] >= 10
        c.execute(
            "INSERT INTO transactions(asset_id,tx_type,quantity,price,trade_date) VALUES (1,'BONUS','5','0','2024-07-01')"
        )
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


# --------------------------------------------------------------------------- bütçe
def test_month_helpers() -> None:
    assert add_months(date(2026, 1, 31), -1) == date(2025, 12, 1)
    assert add_months(date(2026, 11, 5), 2) == date(2027, 1, 1)
    assert month_start(date(2026, 3, 17)) == date(2026, 3, 1)


def test_month_summary_budget_and_savings(container) -> None:
    c = container
    market = cat(c, "Market")
    c.budget.set_budget(market.id, D("5000"))
    flow(c, "Maaş", "50000")
    flow(c, "Market", "6000")
    flow(c, "Faturalar", "4000")
    flow(c, "Maaş", "10", add_months(TODAY, -1))  # geçen ay, bu aya karışmaz

    m = c.budget.month_summary(TODAY)
    assert (m.income, m.expense, m.net) == (D("50000"), D("10000"), D("40000"))
    assert m.savings_rate == D("80")
    over = m.over_budget
    assert [ln.category.name for ln in over] == ["Market"]
    assert over[0].remaining == D("-1000") and over[0].used_pct == D("120")
    assert m.budget_total == D("5000")
    assert c.budget.investable_amount(TODAY) == D("40000")
    c.budget.set_invest_share(D("0.5"))
    assert c.budget.investable_amount(TODAY) == D("20000")

    hist = c.budget.history(3)
    assert len(hist) == 3 and hist[-1].month == month_start(TODAY)
    assert hist[-2].income == D("10")
    assert c.budget.average_savings_rate(3) is not None


def test_category_rules(container) -> None:
    c = container
    with pytest.raises(DuplicateError):
        c.budget.add_category(Category("market", FlowKind.EXPENSE))
    with pytest.raises(ValidationError):
        c.budget.set_budget(cat(c, "Maaş").id, D("100"))
    flow(c, "Market", "10")
    with pytest.raises(ValidationError):
        c.budget.delete_category(cat(c, "Market").id)
    new = c.budget.add_category(Category("Evcil hayvan", FlowKind.EXPENSE, D("500")))
    assert c.budget.rename_category(new.id, "Kedi").name == "Kedi"
    c.budget.delete_category(new.id)
    with pytest.raises(ValidationError):
        c.budget.get_or_create_category("Maaş", FlowKind.EXPENSE)
    with pytest.raises(ValidationError):
        c.budget.set_savings_target(D("2"))


# --------------------------------------------------------------------------- analiz
def test_rules_roundtrip_and_validation(container) -> None:
    c = container
    rules = c.insights.rules()
    assert sum(rules.targets.values()) == 1
    assert rules.declaration_limit is None
    rules.declaration_limit = D("30000")
    rules.targets[Purpose.GROWTH] = D("0.40")
    with pytest.raises(ValidationError):
        c.insights.save_rules(rules)  # toplam %105
    rules.targets[Purpose.DIVIDEND] = D("0.20")
    c.insights.save_rules(rules)
    again = c.insights.rules()
    assert again.targets[Purpose.GROWTH] == D("0.40") and again.declaration_limit == D("30000")
    assert PlanRules().experiment_budget == D("0.10")


def test_purpose_allocation_and_plan(portfolio) -> None:
    c, *_ = portfolio
    s = c.portfolio.summary(Currency.TRY)
    rows = {r.purpose: r for r in c.insights.purpose_allocation(s)}
    assert rows[Purpose.UNASSIGNED].symbols == ("TTE",)
    assert rows[Purpose.EXPERIMENT].value == 0
    assert sum(r.weight for r in rows.values()).quantize(D("0.0001")) == 1

    plan = c.insights.monthly_plan(D("10000"), s)
    assert sum(p.amount for p in plan) == D("10000")
    by = {p.purpose: p for p in plan}
    # Deneme boş ve dividend (AAPL) hedefin çok üstünde → oraya para gitmez
    assert by[Purpose.DIVIDEND].amount == 0
    assert by[Purpose.EXPERIMENT].amount > 0
    assert by[Purpose.GROWTH].symbols == ("THYAO",)


def test_real_returns_with_cpi_and_fx_history(portfolio, provider) -> None:
    c, thy, aapl, *_ = portfolio
    provider.history["USDTRY=X"] = [(date(2024, 1, 1), D("30"))]
    c.cpi.replace_all({"2024-01": D("100"), "2026-01": D("200")})
    report = c.insights.real_returns()
    rows = {r.asset.symbol: r for r in report.rows}
    assert report.cpi_month == "2026-01"
    assert rows["THYAO"].invested == D("25000")
    assert rows["THYAO"].invested_real == D("50000")
    assert rows["THYAO"].value == D("30000")
    assert rows["THYAO"].real_pct == D("-40")
    assert rows["AAPL"].invested == D("1500") * 30  # işlem günü kuru
    assert not rows["AAPL"].approx_fx
    assert report.real_pct is not None and report.nominal_pct is not None


def test_real_returns_without_cpi_or_fx(portfolio) -> None:
    c, *_ = portfolio
    report = c.insights.real_returns()
    assert not report.has_cpi and report.invested_real is None and report.real_pct is None
    assert {r.asset.symbol: r.approx_fx for r in report.rows}["AAPL"] is True


def test_sell_reduces_invested_and_bonus_is_free(portfolio) -> None:
    c, thy, *_ = portfolio
    add_tx(c, thy, TxType.BONUS, "100", "0", date(2024, 2, 1))
    add_tx(c, thy, TxType.SELL, "50", "300", date(2024, 3, 1))
    row = next(r for r in c.insights.real_returns().rows if r.asset.symbol == "THYAO")
    assert row.invested == D("25000") - D("15000")
    assert row.value == D("150") * 300


def test_health_alerts_and_scenario(portfolio) -> None:
    c, *_ = portfolio
    s = c.portfolio.summary(Currency.TRY)
    health = c.insights.health(s, c.insights.real_returns())
    keys = {comp.key: comp for comp in health.components}
    assert not keys["inflation"].available and not keys["savings"].available
    assert keys["concentration"].points < keys["concentration"].max_points  # AAPL ≈ %56
    assert health.score is not None and 0 <= health.score <= 100
    assert health.potential[0].missing >= health.potential[-1].missing

    texts = " | ".join(a.text for a in c.insights.alerts(s, c.insights.real_returns()))
    assert "AAPL portföyün" in texts
    assert "Amacı atanmamış" in texts
    assert "TÜFE verisi girilmemiş" in texts

    shocked = c.insights.scenario(s, {AssetClass.BIST_STOCK: D("-0.5")}, D("0"))
    assert shocked == s.total_value - D("15000")
    assert c.insights.scenario(s, {}, D("0.1")) == s.total_value + D("8000")


def test_declaration_status_and_alert(portfolio, provider) -> None:
    c, thy, aapl, *_ = portfolio
    year = TODAY.year
    c.dividends.record(
        Dividend(
            aapl.id, date(year, 1, 15), D("1"), D("10"), Currency.USD, DividendStatus.PAID, withholding_rate=D("0.2")
        )
    )
    c.dividends.record(Dividend(thy.id, date(year, 1, 15), D("5"), D("100"), Currency.TRY, DividendStatus.PAID))
    status = c.insights.declaration_status(year)
    assert status.gross_try == D("400") and status.approx_fx  # 10 USD × 40, kur geçmişi yok
    assert status.ratio is None
    rules = c.insights.rules()
    rules.declaration_limit = D("450")
    c.insights.save_rules(rules)
    status = c.insights.declaration_status(year)
    assert status.ratio is not None and not status.exceeded
    s = c.portfolio.summary(Currency.TRY)
    assert any(a.level == "warning" and "beyan sınırının" in a.text for a in c.insights.alerts(s))
    rules.declaration_limit = D("100")
    c.insights.save_rules(rules)
    assert c.insights.alerts(s)[0].level == "error"


def test_upcoming_dividend_and_budget_alerts(portfolio) -> None:
    c, thy, *_ = portfolio
    c.dividends.record(Dividend(thy.id, TODAY + timedelta(days=3), D("1"), D("100"), Currency.TRY))
    c.budget.set_budget(cat(c, "Market").id, D("100"))
    flow(c, "Market", "250")
    texts = [a.text for a in c.insights.alerts(c.portfolio.summary(Currency.TRY))]
    assert any("temettüsü" in t for t in texts)
    assert any("Market bu ay bütçeyi 150 TL aştı" in t for t in texts)


# --------------------------------------------------------------------------- veri aktarımı
def test_json_roundtrip_v2(portfolio, tmp_path) -> None:
    c, *_ = portfolio
    flow(c, "Maaş", "1000", note="Ekim")
    c.budget.add_category(Category("Kedi", FlowKind.EXPENSE, D("300")))
    flow(c, "Kedi", "120")
    c.cpi.replace_all({"2026-01": D("100")})
    payload = c.transfer.export_json()

    from divitrack.bootstrap import build_container
    from divitrack.config import Settings

    other = build_container(Settings(db_path=tmp_path / "other.db"), use_default_provider=False)
    report = other.transfer.import_json(payload)
    assert report.cash_flows == 2 and report.categories_created == 1
    assert {a.symbol: a.purpose for a in other.portfolio.assets.list()}["THYAO"] is Purpose.GROWTH
    assert other.cpi.all() == {"2026-01": D("100")}
    assert other.budget.categories.find("Kedi").monthly_budget == D("300")
    again = other.transfer.import_json(payload)  # tekrar içe aktarmak kopya üretmez
    assert again.cash_flows == 0
    other.transfer.import_json(payload, replace=True)
    assert len(other.budget.flows.list()) == 2


def test_cash_flow_csv(container) -> None:
    c = container
    text = "date;category;amount;note\n2026-01-05;Maaş;45.000,50;Ocak\n2026-01-06;Kahve;-120;\n"
    report = c.transfer.import_cash_flows_csv(text.encode("utf-8-sig"))
    assert report.cash_flows == 2 and report.categories_created == 1
    kahve = c.budget.categories.find("Kahve")
    assert kahve is not None and kahve.kind is FlowKind.EXPENSE
    out = c.transfer.export_cash_flows_csv()
    assert "2026-01-05,Maaş,INCOME,45000.5,Ocak" in out
    with pytest.raises(ImportFormatError):
        c.transfer.import_cash_flows_csv("date,category\n2026-01-01,x\n")
    with pytest.raises(ImportFormatError):
        c.transfer.import_cash_flows_csv("date,category,amount,kind\n2026-01-01,Maaş,5,Gider\n")
    with pytest.raises(ImportFormatError):
        c.transfer.import_cash_flows_csv("date,category,amount\n2026-99-01,Maaş,5\n")
