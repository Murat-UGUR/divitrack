"""0.8 saf hesapları: risk, vergi endekslemesi, alım planı, hızlı giriş, mevduat faizi."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from divitrack.core.entities import Transaction
from divitrack.core.enums import Currency, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.core.gold_plan import BuyPlan, evaluate_plan
from divitrack.core.quick_entry import parse_entry
from divitrack.core.returns import accrued_unit_price, open_lots
from divitrack.core.risk import correlation, daily_returns, drawdown, performance_index, volatility
from divitrack.core.tax import indexation_factor, realized_lots, taxable_gains

TODAY = date(2026, 11, 22)


def tx(t: TxType, q: str, p: str, d: date, fee: str = "0", id_: int | None = None) -> Transaction:
    return Transaction(asset_id=1, tx_type=t, quantity=D(q), price=D(p), trade_date=d, fee=D(fee), id=id_)


# --------------------------------------------------------------------------- risk
def test_performance_index_ignores_new_money() -> None:
    d = date(2026, 1, 1)
    # 100 → yeni 100 TL yatır (değer 200) → %10 düşüş (180)
    pts = [(d, D("100"), D("100")), (d + timedelta(1), D("200"), D("200")), (d + timedelta(2), D("180"), D("200"))]
    idx = performance_index(pts)
    assert [round(v, 4) for _, v in idx] == [100.0, 100.0, 90.0]
    dd = drawdown(idx)
    assert dd is not None and dd.max_pct == D("-10.0") and dd.current_pct == D("-10.0") and not dd.recovered
    assert dd.trough == d + timedelta(2)


def test_drawdown_recovered() -> None:
    d = date(2026, 1, 1)
    idx = [(d, 100.0), (d + timedelta(1), 80.0), (d + timedelta(2), 120.0)]
    dd = drawdown(idx)
    assert dd is not None and dd.max_pct == D("-20.0") and dd.recovered and dd.current_pct == 0


def test_volatility_and_correlation() -> None:
    d = date(2026, 1, 1)
    a = {d + timedelta(i): D(str(100 * (1.01 if i % 2 else 0.99) ** 1 + i * 0.1)) for i in range(40)}
    ra = daily_returns(a)
    assert volatility(ra) is not None and volatility(ra) > 0
    assert correlation(ra, ra) == D("1.0")
    inv = {k: -v for k, v in ra.items()}
    assert correlation(ra, inv) == D("-1.0")
    assert volatility(dict(list(ra.items())[:5])) is None


# --------------------------------------------------------------------------- vergi
def test_realized_lots_fifo_with_fees() -> None:
    txs = [
        tx(TxType.BUY, "10", "30", date(2025, 1, 10), fee="1", id_=1),
        tx(TxType.BUY, "10", "40", date(2025, 6, 10), id_=2),
        tx(TxType.SELL, "15", "50", date(2026, 3, 1), fee="1.5", id_=3),
    ]
    lots = realized_lots(txs)
    assert [(lot.buy_date, lot.quantity) for lot in lots] == [(date(2025, 1, 10), D("10")), (date(2025, 6, 10), D("5"))]
    assert lots[0].cost == D("301") and lots[0].proceeds == D("499")  # (750−1,5)/15 × 10
    assert lots[1].cost == D("200")


def test_indexation_threshold() -> None:
    ppi = {"2024-12": D("100"), "2025-05": D("105"), "2026-02": D("130")}
    assert indexation_factor(ppi, date(2025, 1, 10), date(2026, 3, 1)) == (D("1.3"), True)
    assert indexation_factor(ppi, date(2025, 6, 10), date(2026, 3, 1))[0] == D("130") / D("105")
    assert indexation_factor({"2024-12": D("100"), "2025-01": D("105")}, date(2025, 1, 5), date(2025, 2, 5)) == (
        D("1"),
        True,
    )
    assert indexation_factor({}, date(2025, 1, 5), date(2025, 2, 5)) == (D("1"), False)


def test_taxable_gains_in_try() -> None:
    lots = realized_lots(
        [tx(TxType.BUY, "10", "30", date(2025, 1, 10), id_=1), tx(TxType.SELL, "10", "50", date(2026, 3, 1), id_=2)]
    )
    fx = {date(2025, 1, 10): D("35"), date(2026, 3, 1): D("45")}
    ppi = {"2024-12": D("100"), "2026-02": D("130")}
    (g,) = taxable_gains(lots, fx, ppi)
    assert g.cost_try == D("10500") and g.indexed_cost_try == D("13650") and g.proceeds_try == D("22500")
    assert g.gain_try == D("8850") and g.indexed
    assert taxable_gains(lots, {}, ppi) == []  # kur yoksa atlanır


# --------------------------------------------------------------------------- alım planı
def test_buy_plan_progress_and_dip() -> None:
    plan = BuyPlan("XAU-GRAM", D("10"), "2026-09", dip_price=D("5800"), dip_extra=D("5"))
    buys = [
        (date(2026, 9, 10), D("8"), D("44000")),
        (date(2026, 10, 20), D("12"), D("70000")),
        (date(2026, 11, 3), D("4"), D("23000")),
    ]
    st = evaluate_plan(plan, buys, (D("20"), D("120000")), TODAY, D("5751"))
    assert [m.month for m in st.months] == ["2026-09", "2026-10", "2026-11"]
    assert [m.done for m in st.months] == [False, True, False]
    assert st.this_month_remaining == D("6") and st.dip_active and st.suggested == D("11")
    assert st.behind == D("6")  # 30 planlı − 24 alınan
    assert st.months[0].average_after == D("164000") / D("28")
    assert st.suggested_amount == D("11") * D("5751")


def test_buy_plan_validation() -> None:
    with pytest.raises(ValidationError):
        BuyPlan("XAU-GRAM", D("0"), "2026-01")
    with pytest.raises(ValidationError):
        BuyPlan("XAU-GRAM", D("1"), "2026-13")


def test_buy_plan_future_start() -> None:
    st = evaluate_plan(BuyPlan("X", D("1"), "2027-01"), [], (D("0"), D("0")), TODAY, None)
    assert st.months == [] and st.suggested == 0 and not st.dip_active


# --------------------------------------------------------------------------- hızlı giriş
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5 gr altın 5750", (TxType.BUY, D("5"), "XAU-GRAM", D("5750"), TODAY, None)),
        (
            "aldım 3,25 gram altın 5480 tl'den 15.10.2026",
            (TxType.BUY, D("3.25"), "XAU-GRAM", D("5480"), date(2026, 10, 15), Currency.TRY),
        ),
        ("sat 100 EREGL 45,5", (TxType.SELL, D("100"), "EREGL", D("45.5"), TODAY, None)),
        ("12 SCHD 31.5$ 2026-11-18", (TxType.BUY, D("12"), "SCHD", D("31.5"), date(2026, 11, 18), Currency.USD)),
        ("40,5 gr gümüş 75,40 dün", (TxType.BUY, D("40.5"), "XAG-GRAM", D("75.40"), TODAY - timedelta(1), None)),
        ("25 thyao @1.234,50", (TxType.BUY, D("25"), "THYAO", D("1234.50"), TODAY, None)),
    ],
)
def test_parse_entry(text: str, expected: tuple) -> None:
    p = parse_entry(text, TODAY)
    assert (p.tx_type, p.quantity, p.symbol, p.price, p.trade_date, p.currency) == expected


@pytest.mark.parametrize(
    "text", ["", "altın 5750", "5 10 20 altın", "5 3000", "5 altın 100 01.01.2030", "5 altın 100 31.02.2026"]
)
def test_parse_entry_errors(text: str) -> None:
    with pytest.raises(ValidationError):
        parse_entry(text, TODAY)


# --------------------------------------------------------------------------- mevduat faizi
def test_accrued_unit_price() -> None:
    lots = open_lots([tx(TxType.BUY, "1000", "1", date(2025, 11, 22)), tx(TxType.BUY, "1000", "1", TODAY)])
    unit = accrued_unit_price(lots, D("40"), TODAY)
    # ilk 1000 bir yılda %40 büyür, ikinci 1000 bugün yatırıldı → birim (1400+1000)/2000
    assert unit is not None and abs(unit - D("1.2")) < D("0.0001")
    assert accrued_unit_price([], D("40"), TODAY) is None
