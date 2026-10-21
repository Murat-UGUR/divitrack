from datetime import date
from decimal import Decimal as D

import pytest

from divitrack.core.entities import Asset, CashFlow, Transaction
from divitrack.core.enums import AssetClass, Currency, Purpose, TxType
from divitrack.core.exceptions import ImportFormatError, ValidationError
from divitrack.core.statement import (
    ColumnMap,
    match_category,
    parse_date,
    parse_statement,
    read_table,
    tr_lower,
)

STATEMENT = (
    "Hesap No;TR00 0000\n"
    "Müşteri;Örnek\n"
    "\n"
    "Tarih;Açıklama;Tutar;Bakiye\n"
    "01.10.2026;MAAŞ ÖDEMESİ EKİM;65.000,00;70.000,00\n"
    "03.10.2026;MİGROS İSTANBUL;-1.250,40;68.749,60\n"
    "04.10.2026;SHELL AKARYAKIT;-2.000,00;66.749,60\n"
    ";Devreden bakiye;;66.749,60\n"
    "Toplam;;61.749,60;\n"
)


def cat_id(c, name: str) -> int:
    found = c.budget.categories.find(name)
    assert found is not None and found.id is not None
    return found.id


# --------------------------------------------------------------------------- saf fonksiyonlar
def test_tr_lower_and_dates() -> None:
    assert tr_lower("  MİGROS   ISPARTA ") == "migros ısparta"
    assert parse_date("03.10.2026") == date(2026, 10, 3)
    assert parse_date("2026-10-03") == date(2026, 10, 3)
    assert parse_date("03/10/2026") == date(2026, 10, 3)
    with pytest.raises(ValidationError):
        parse_date("Toplam")


def test_read_table_skips_preamble_and_parses_rows() -> None:
    header, rows = read_table(STATEMENT.encode("cp1254"))
    assert header == ["Tarih", "Açıklama", "Tutar", "Bakiye"]
    parsed = parse_statement(rows, ColumnMap(date=0, description=1, amount=2), first_line=5)
    assert [r.amount for r in parsed] == [D("65000.00"), D("-1250.40"), D("-2000.00")]
    assert parsed[1].description == "MİGROS İSTANBUL" and parsed[1].is_expense
    assert parsed[0].line == 5


def test_debit_credit_columns_and_positive_expenses() -> None:
    rows = [["2026-10-01", "Kira", "15.000", ""], ["2026-10-02", "İade", "", "100"], ["2026-10-03", "x", "", ""]]
    parsed = parse_statement(rows, ColumnMap(date=0, description=1, debit=2, credit=3))
    assert [r.amount for r in parsed] == [D("-15000"), D("100")]  # boş tutarlı satır atlanır
    card = parse_statement([["2026-10-01", "Kahve", "85,50"]], ColumnMap(0, 1, amount=2, expense_positive=True))
    assert card[0].amount == D("-85.50")
    with pytest.raises(ValidationError):
        parse_statement(rows, ColumnMap(date=0, description=1))
    with pytest.raises(ImportFormatError):
        parse_statement([["2026-10-01", "x", "abc"]], ColumnMap(0, 1, amount=2))
    with pytest.raises(ImportFormatError):
        read_table(b"\n\n")


def test_match_category_prefers_longer_patterns() -> None:
    rules = [("migros", 1), ("migros sanal", 2), ("SHELL", 3)]
    assert match_category("MİGROS SANAL MARKET", rules) == 2
    assert match_category("Migros İstanbul", rules) == 1
    assert match_category("shell akaryakıt", rules) == 3
    assert match_category("Bilinmeyen", rules) is None


# --------------------------------------------------------------------------- servis
def test_statement_import_with_rules_overrides_and_duplicates(container) -> None:
    c = container
    market, fuel = cat_id(c, "Market"), cat_id(c, "Ulaşım")
    c.budget.add_rule("migros", market)
    c.budget.add_rule("akaryakıt", fuel)
    c.budget.add_rule("maaş", market)  # yanlış türde kural → varsayılana düşmeli
    with pytest.raises(ValidationError):
        c.budget.add_rule("a", market)
    assert len(c.budget.list_rules()) == 3

    _, rows = read_table(STATEMENT)
    parsed = parse_statement(rows, ColumnMap(date=0, description=1, amount=2))
    preview = c.budget.preview_statement(parsed)
    assert [p.category_id for p in preview] == [market, market, fuel]
    assert not any(p.duplicate for p in preview)

    defaults = {"income_category_id": cat_id(c, "Maaş"), "expense_category_id": cat_id(c, "Diğer gider")}
    report = c.budget.import_statement(parsed, **defaults)
    assert (report.imported, report.by_rule, report.defaulted) == (3, 2, 1)
    assert "3 kayıt" in str(report)
    month = c.budget.month_summary(date(2026, 10, 1))
    assert month.income == D("65000") and month.expense == D("3250.40")

    again = c.budget.import_statement(parsed, **defaults)
    assert again.imported == 0 and again.duplicates == 3

    c.budget.add_flow(CashFlow(cat_id(c, "Market"), date(2026, 10, 9), D("10"), "Ek"))
    other = parse_statement([["09.10.2026", "Kitap", "-50"]], ColumnMap(0, 1, amount=2))
    manual = c.budget.import_statement(other, **defaults, overrides={other[0].line: cat_id(c, "Eğitim")})
    assert manual.manual == 1
    assert any(f.category_id == cat_id(c, "Eğitim") for f in c.budget.flows.list())

    with pytest.raises(ValidationError):
        c.budget.import_statement(parsed, income_category_id=market, expense_category_id=market)
    rule_id = c.budget.list_rules()[0][0]
    c.budget.delete_rule(rule_id)
    assert len(c.budget.list_rules()) == 2


def test_rules_survive_json_roundtrip(container, tmp_path) -> None:
    from divitrack.bootstrap import build_container
    from divitrack.config import Settings

    c = container
    c.budget.add_rule("netflix", cat_id(c, "Yeme-içme / eğlence"))
    other = build_container(Settings(db_path=tmp_path / "o.db"), use_default_provider=False)
    other.transfer.import_json(c.transfer.export_json())
    assert [(p, other.budget.categories.get(cid).name) for _, p, cid in other.budget.list_rules()] == [
        ("netflix", "Yeme-içme / eğlence")
    ]


def test_score_history_and_report_cards(container) -> None:
    c = container
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY, purpose=Purpose.DIVIDEND))
    cash = c.portfolio.add_asset(Asset("NAKIT", AssetClass.CASH, Currency.TRY))
    for a, q, p in ((thy, "100", "250"), (cash, "1000", "1")):
        c.portfolio.add_transaction(
            Transaction(asset_id=a.id, tx_type=TxType.BUY, quantity=D(q), price=D(p), trade_date=date(2024, 1, 2))
        )
    c.cpi.replace_all({"2024-01": D("100"), "2026-01": D("200")})
    s = c.portfolio.summary(Currency.TRY)
    real = c.insights.real_returns()
    health = c.insights.health(s, real)

    c.insights.record_score(health, day=date(2026, 9, 5))
    c.insights.record_score(health, day=date(2026, 10, 5))
    c.insights.record_score(health, day=date(2026, 10, 20))  # aynı ay üzerine yazar
    history = c.insights.score_history()
    assert [m for m, _ in history] == ["2026-09", "2026-10"]
    assert history[-1][1] == health.score

    cards = {card.asset.symbol: card for card in c.insights.report_cards(s, real, {thy.id: None})}
    assert "Tek kalem sınırı aşıldı" in cards["THYAO"].flags  # %96,8
    assert "Enflasyonun gerisinde" in cards["THYAO"].flags
    assert "Temettü amacında ama temettü yok" in cards["THYAO"].flags
    assert cards["THYAO"].grade == "Dikkat"
    assert cards["NAKIT"].flags == ("Amaç atanmamış", "Enflasyonun gerisinde")
    assert c.insights.report_cards(s, None, {thy.id: D("5")})[0].dividend_yield in (None, D("5"))
