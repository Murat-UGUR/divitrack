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
from divitrack.core.entities import Asset, Dividend, Transaction  # noqa: E402
from divitrack.core.enums import AssetClass, Currency, DividendStatus, TxType  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
PAGES = ["dashboard", "holdings", "dividends", "settings"]


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
    cash = c.portfolio.add_asset(Asset("NAKIT-TRY", AssetClass.CASH, Currency.TRY))
    fund = c.portfolio.add_asset(Asset("BES-FON", AssetClass.CASH, Currency.TRY, manual_price=D("1.5")))
    stock = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    for a, q, p in ((cash, "1000", "1"), (fund, "200", "1.2"), (stock, "10", "250")):
        c.portfolio.add_transaction(
            Transaction(asset_id=a.id, tx_type=TxType.BUY, quantity=D(q), price=D(p), trade_date=date(2024, 1, 2))
        )
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
