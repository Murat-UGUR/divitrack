from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from divitrack.core.entities import Asset, Dividend, Transaction
from divitrack.core.enums import AssetClass, Currency, DividendStatus, TxType
from divitrack.core.exceptions import DuplicateError, ImportFormatError, InsufficientQuantityError
from divitrack.db.database import Database
from divitrack.db.schema import SCHEMA_VERSION
from divitrack.services.dividend_engine import EventKind


def buy(c, asset, qty, price, d=date(2024, 1, 2), fee="0"):
    return c.portfolio.add_transaction(
        Transaction(asset_id=asset.id, tx_type=TxType.BUY, quantity=D(qty), price=D(price), trade_date=d, fee=D(fee))
    )


@pytest.fixture
def seeded(container):
    c = container
    thy = c.portfolio.add_asset(Asset("THYAO.IS", AssetClass.BIST_STOCK, Currency.TRY))
    aapl = c.portfolio.add_asset(Asset("AAPL", AssetClass.US_STOCK, Currency.USD, name="Apple"))
    cash = c.portfolio.add_asset(Asset("NAKIT-USD", AssetClass.CASH, Currency.USD))
    buy(c, thy, "100", "250")
    buy(c, aapl, "10", "150")
    buy(c, cash, "1000", "1")
    return c, thy, aapl, cash


def test_schema_migrated(tmp_path) -> None:
    db = Database(tmp_path / "x.db")
    assert db.schema_version() == SCHEMA_VERSION
    Database(tmp_path / "x.db")  # tekrar açmak güvenli


def test_bist_suffix_normalized_and_duplicate_rejected(seeded) -> None:
    c, thy, *_ = seeded
    assert thy.symbol == "THYAO"
    with pytest.raises(DuplicateError):
        c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))


def test_summary_in_try(seeded) -> None:
    c, thy, aapl, cash = seeded
    s = c.portfolio.summary(Currency.TRY)
    # THYAO 100×300 = 30.000; AAPL 10×200×40 = 80.000; Nakit 1000×40 = 40.000
    assert s.total_value == D("150000")
    assert s.total_cost == D("25000") + D("60000") + D("40000")
    assert s.unrealized_pnl == D("25000")
    assert s.day_change == D("100") * 10 + D("10") * 2 * 40
    assert s.allocation_by_class[AssetClass.CASH] == D("40000")
    assert not s.unpriced


def test_summary_in_usd(seeded) -> None:
    c, *_ = seeded
    # TRYUSD=X sağlayıcıda yok → USDTRY=X tersine çevrilerek kullanılır
    s = c.portfolio.summary(Currency.USD)
    assert not s.unpriced
    assert s.total_value == D("30000") / 40 + D("2000") + D("1000")


def test_oversell_blocked_and_delete_guard(seeded) -> None:
    c, thy, *_ = seeded
    with pytest.raises(InsufficientQuantityError):
        c.portfolio.add_transaction(
            Transaction(
                asset_id=thy.id, tx_type=TxType.SELL, quantity=D("101"), price=D("1"), trade_date=date(2024, 2, 1)
            )
        )
    sell = c.portfolio.add_transaction(
        Transaction(asset_id=thy.id, tx_type=TxType.SELL, quantity=D("50"), price=D("300"), trade_date=date(2024, 3, 1))
    )
    first_buy = c.portfolio.transactions.list(thy.id)[0]
    with pytest.raises(InsufficientQuantityError):
        c.portfolio.delete_transaction(first_buy.id)
    c.portfolio.delete_transaction(sell.id)
    assert len(c.portfolio.transactions.list(thy.id)) == 1


def test_dividend_events_and_yield(seeded, provider) -> None:
    c, thy, aapl, _ = seeded
    today = date.today()
    provider.dividends["AAPL"] = [(today - timedelta(days=d), D("0.25")) for d in (30, 120, 210, 300)]
    c.dividends.today = lambda: today
    pairs = c.portfolio.positions()
    events = c.dividends.events(pairs)
    projected = [e for e in events if e.kind is EventKind.PROJECTED]
    assert len(projected) == 4 and all(e.symbol == "AAPL" for e in projected)
    assert projected[0].gross == D("2.5") and projected[0].net == D("2.5") * D("0.8")

    state = dict((a.id, s) for a, s in pairs)[aapl.id]
    info = c.dividends.yield_info(aapl, state, c.market.quotes_for([aapl])[aapl.id])
    assert info.ttm_per_share == D("1") and info.current_yield == D("0.5") and info.frequency == 4

    # Kullanıcı 'beklenen' kaydı girerse tahmin üretilmez
    c.dividends.record(
        Dividend(
            asset_id=aapl.id,
            pay_date=today + timedelta(days=10),
            amount_per_share=D("0.3"),
            quantity=D("10"),
            currency=Currency.USD,
        )
    )
    kinds = {e.kind for e in c.dividends.events(pairs) if e.asset_id == aapl.id}
    assert kinds == {EventKind.EXPECTED}


def test_suggest_from_history_uses_quantity_at_ex_date(seeded, provider) -> None:
    c, thy, *_ = seeded
    buy(c, thy, "50", "260", d=date(2024, 6, 1))
    provider.dividends["THYAO.IS"] = [
        (date(2023, 6, 1), D("1")),
        (date(2024, 5, 1), D("2")),
        (date(2024, 7, 1), D("3")),
    ]
    sugg = c.dividends.suggest_from_history(thy, c.portfolio.transactions.list(thy.id))
    assert [(s.ex_date, s.quantity) for s in sugg] == [(date(2024, 5, 1), D(100)), (date(2024, 7, 1), D(150))]
    assert sugg[0].withholding_rate == D("0.15") and sugg[0].status is DividendStatus.PAID


def test_snapshot_record_and_backfill(seeded, provider) -> None:
    c, thy, aapl, cash = seeded
    today = date.today()
    c.snapshots.today = lambda: today
    days = [today - timedelta(days=i) for i in (3, 2, 1)]
    provider.history = {
        "THYAO.IS": [(d, D("280")) for d in days],
        "AAPL": [(d, D("190")) for d in days],
        "USDTRY=X": [(d, D("39")) for d in days],
    }
    # işlemleri yakın tarihe taşı ki backfill penceresine girsin
    for t in c.portfolio.transactions.list():
        c.portfolio.transactions.delete(t.id)
    for a, q, p in ((thy, "100", "250"), (aapl, "10", "150"), (cash, "1000", "1")):
        buy(c, a, q, p, d=days[0])
    n = c.snapshots.backfill(Currency.TRY)
    assert n == 3
    first = c.snapshots.history(Currency.TRY)[0]
    assert first.total_value == D(28000) + D(10 * 190 * 39) + D(1000 * 39)

    snap = c.snapshots.record(c.portfolio.summary(Currency.TRY))
    assert snap is not None and len(c.snapshots.history(Currency.TRY)) == 4


def test_json_roundtrip(seeded, tmp_path) -> None:
    from divitrack.bootstrap import build_container
    from divitrack.config import Settings

    c, thy, *_ = seeded
    c.dividends.record(
        Dividend(
            asset_id=thy.id,
            pay_date=date(2024, 5, 1),
            amount_per_share=D("3.5"),
            quantity=D("100"),
            currency=Currency.TRY,
            status=DividendStatus.PAID,
            withholding_rate=D("0.15"),
        )
    )
    dump = c.transfer.export_json()

    other = build_container(Settings(db_path=tmp_path / "other.db"), use_default_provider=False)
    report = other.transfer.import_json(dump)
    assert (report.assets_created, report.transactions, report.dividends) == (3, 3, 1)
    again = other.transfer.import_json(dump)  # tekrar içe aktarma kopya üretmez
    assert again.transactions == 0 and again.skipped == 4
    with pytest.raises(ImportFormatError):
        other.transfer.import_json('{"foo": 1}')


def test_csv_import_export(container) -> None:
    text = (
        "date;symbol;asset_class;currency;type;quantity;price;fee;note\n"
        "2024-01-05;ASELS;BIST_STOCK;TRY;BUY;10;55,5;1;ilk alım\n"
        "2024-02-05;ASELS.IS;BIST_STOCK;TRY;SELL;4;60;0;\n"
        "2024-03-01;SCHD;US_STOCK;USD;BUY;3;27.1;;\n"
    )
    report = container.transfer.import_transactions_csv(text.encode("utf-8-sig"))
    assert report.assets_created == 2 and report.transactions == 3
    out = container.transfer.export_transactions_csv()
    assert "ASELS,BIST_STOCK,TRY,BUY,10,55.5,1,ilk alım" in out

    bad = "date,symbol,asset_class,currency,type,quantity,price\n2024-01-01,X,US_STOCK,USD,SELL,5,1\n"
    with pytest.raises(InsufficientQuantityError):
        container.transfer.import_transactions_csv(bad)
    assert container.portfolio.assets.find("X", AssetClass.US_STOCK) is None  # hiçbir şey yazılmadı
    with pytest.raises(ImportFormatError):
        container.transfer.import_transactions_csv("a,b\n1,2\n")


def test_backup_bytes(container) -> None:
    data = container.transfer.backup_db()
    assert data.startswith(b"SQLite format 3")
