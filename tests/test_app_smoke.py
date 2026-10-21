"""Streamlit arayüzünün her sayfasının hatasız açıldığını doğrular.

Streamlit/plotly kurulu değilse atlanır; CI'da (requirements-dev) çalışır.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

from streamlit.testing.v1 import AppTest  # noqa: E402

from divitrack.bootstrap import build_container  # noqa: E402
from divitrack.config import Settings  # noqa: E402
from divitrack.core.entities import Asset, CashFlow, Dividend, Transaction  # noqa: E402
from divitrack.core.enums import AssetClass, Currency, DividendStatus, Purpose, TxType  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
PAGES = ["dashboard", "holdings", "dividends", "analysis", "budget", "plan", "settings"]


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch) -> Path:
    db = tmp_path / "ui.db"
    monkeypatch.setenv("DIVITRACK_DB_PATH", str(db))
    monkeypatch.setenv("DIVITRACK_OFFLINE", "1")
    # Önceki testin önbelleğe aldığı container'ı temizle
    from divitrack.ui.state import get_container

    get_container.clear()
    return db


def _seed(db: Path) -> None:
    c = build_container(Settings(db_path=db, offline=True), use_default_provider=False)
    cash = c.portfolio.add_asset(Asset("NAKIT-TRY", AssetClass.CASH, Currency.TRY, purpose=Purpose.DEFENSIVE))
    fund = c.portfolio.add_asset(
        Asset("BES-FON", AssetClass.FUND, Currency.TRY, manual_price=D("1.5"), purpose=Purpose.GROWTH)
    )
    stock = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    for a, q, p in ((cash, "1000", "1"), (fund, "200", "1.2"), (stock, "10", "250")):
        c.portfolio.add_transaction(
            Transaction(asset_id=a.id, tx_type=TxType.BUY, quantity=D(q), price=D(p), trade_date=date(2024, 1, 2))
        )
    c.portfolio.add_transaction(
        Transaction(asset_id=fund.id, tx_type=TxType.BONUS, quantity=D("20"), price=D("0"), trade_date=date(2024, 6, 1))
    )
    salary = c.budget.categories.find("Maaş")
    market = c.budget.categories.find("Market")
    c.budget.set_budget(market.id, D("100"))
    c.budget.add_flow(CashFlow(salary.id, date.today(), D("50000")))
    c.budget.add_flow(CashFlow(market.id, date.today(), D("250"), "haftalık"))
    c.cpi.replace_all({"2024-01": D("100"), "2025-01": D("145")})
    rules = c.insights.rules()
    rules.declaration_limit = D("10000")
    c.insights.save_rules(rules)
    c.budget.add_rule("migros", market.id)
    for month in (date(2026, 8, 1), date(2026, 9, 1)):
        c.insights.record_score(c.insights.health(c.portfolio.summary(Currency.TRY)), day=month)
    c.dividends.record(
        Dividend(
            asset_id=stock.id,
            pay_date=date.today(),
            amount_per_share=D("3"),
            quantity=D("10"),
            currency=Currency.TRY,
            status=DividendStatus.PAID,
        )
    )


def _page_script(page: str) -> str:
    return (
        f"import sys\nsys.path.insert(0, {str(ROOT / 'src')!r})\n"
        f"from divitrack.ui.pages import {page}\n{page}.render()\n"
    )


def test_app_entrypoint_starts() -> None:
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "Portföy Özeti"


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("seeded", [False, True])
def test_page_renders(page: str, seeded: bool, _isolated_db: Path) -> None:
    if seeded:
        _seed(_isolated_db)
    at = AppTest.from_string(_page_script(page), default_timeout=60).run()
    assert not at.exception, at.exception


def test_plan_page_suggests_split(_isolated_db: Path) -> None:
    _seed(_isolated_db)
    at = AppTest.from_string(_page_script("plan"), default_timeout=60).run()
    assert not at.exception, at.exception
    assert len(at.dataframe) == 1  # bütçeden gelen tutarla plan tablosu


def test_budget_form_adds_flow(_isolated_db: Path) -> None:
    _seed(_isolated_db)
    at = AppTest.from_string(_page_script("budget"), default_timeout=60).run()
    next(t for t in at.text_input if t.label == "Tutar (TL)").set_value("1.250,50")
    next(b for b in at.button if b.label == "Kaydet").click().run()
    assert not at.exception, at.exception
    c = build_container(Settings(db_path=_isolated_db, offline=True), use_default_provider=False)
    assert any(f.amount == D("1250.50") for f in c.budget.flows.list())
