from datetime import date
from decimal import Decimal as D

import pytest

from divitrack.core.calculations import compute_position
from divitrack.core.entities import CashFlow, Category, Transaction
from divitrack.core.enums import FlowKind, Purpose, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.core.inflation import (
    cpi_at,
    index_from_changes,
    inflation_factor,
    monthly_changes,
    parse_month,
)
from divitrack.core.planning import (
    ScoreComponent,
    allocate,
    apply_shocks,
    drift,
    linear_score,
    score_grade,
    total_score,
    weights,
)


def tx(t: TxType, q: str, p: str, d: date, fee: str = "0", i: int = 0) -> Transaction:
    return Transaction(asset_id=1, tx_type=t, quantity=D(q), price=D(p), trade_date=d, fee=D(fee), id=i)


# --------------------------------------------------------------------------- kurumsal işlemler
def test_bonus_adds_shares_without_cost() -> None:
    s = compute_position(
        [
            tx(TxType.BUY, "100", "50", date(2024, 1, 1)),
            tx(TxType.BONUS, "100", "999", date(2024, 6, 1)),  # fiyat yok sayılır
        ]
    )
    assert s.quantity == D("200")
    assert s.cost_basis == D("5000")
    assert s.avg_cost == D("25")


def test_rights_and_reinvest_behave_like_buys() -> None:
    s = compute_position(
        [
            tx(TxType.BUY, "10", "100", date(2024, 1, 1)),
            tx(TxType.RIGHTS, "10", "1", date(2024, 2, 1)),
            tx(TxType.REINVEST, "1", "0", date(2024, 3, 1)),
        ]
    )
    assert s.quantity == D("21")
    assert s.cost_basis == D("1010")


def test_new_money_flags() -> None:
    assert TxType.BUY.is_new_money and TxType.RIGHTS.is_new_money
    assert not TxType.BONUS.is_new_money and not TxType.REINVEST.is_new_money and not TxType.SELL.is_new_money
    assert Purpose.UNASSIGNED not in Purpose.targeted()
    assert all(p.label and p.hint for p in Purpose)


def test_budget_entities_validate() -> None:
    assert Category("  Market   alışverişi ", FlowKind.EXPENSE).name == "Market alışverişi"
    with pytest.raises(ValidationError):
        Category(" ", FlowKind.EXPENSE)
    with pytest.raises(ValidationError):
        CashFlow(1, date(2026, 1, 1), D("0"))


# --------------------------------------------------------------------------- enflasyon
def test_parse_month_formats() -> None:
    assert parse_month("2026-3") == "2026-03"
    assert parse_month("03.2026") == "2026-03"
    assert parse_month("3/2026") == "2026-03"
    with pytest.raises(ValidationError):
        parse_month("2026-13")
    with pytest.raises(ValidationError):
        parse_month("mart")


def test_inflation_factor_uses_latest_known_month() -> None:
    index = {"2025-01": D("100"), "2025-06": D("120"), "2026-01": D("150")}
    assert cpi_at(index, "2025-03") == D("100")  # önceki en yakın ay
    assert cpi_at(index, "2024-12") is None
    assert inflation_factor(index, date(2025, 1, 15)) == D("1.5")
    assert inflation_factor(index, date(2025, 6, 1), date(2025, 12, 31)) == D("1")
    assert inflation_factor(index, date(2024, 1, 1)) is None
    assert inflation_factor({}, date(2025, 1, 1)) is None


def test_index_from_changes_roundtrip() -> None:
    index = index_from_changes("2025-12", [("2026-01", D("10")), ("2026-02", D("5"))])
    assert index == {"2025-12": D("100"), "2026-01": D("110.0"), "2026-02": D("115.500")}
    changes = monthly_changes(index)
    assert [m for m, _ in changes] == ["2026-01", "2026-02"]
    assert changes[1][1].quantize(D("0.01")) == D("5.00")


# --------------------------------------------------------------------------- dağılım ve plan
def test_weights_and_drift() -> None:
    assert weights({"a": D(0)}) == {"a": D(0)}
    d = drift({"a": D(75), "b": D(25)}, {"a": D("0.5"), "b": D("0.5")})
    assert d == {"a": D("25.00"), "b": D("-25.00")}


def test_allocate_fills_gaps_first() -> None:
    values = {"def": D(600), "gro": D(200), "div": D(200)}
    targets = {"def": D("0.4"), "gro": D("0.4"), "div": D("0.2")}
    # Yeni toplam 1200: hedefler 480 / 480 / 240 → açıklar 0 / 280 / 40 = 320 > 200
    split = allocate(D(200), values, targets)
    assert split["def"] == 0
    assert split["gro"] == D(200) * 280 / 320
    assert sum(split.values()) == D(200)


def test_allocate_spreads_rest_by_target() -> None:
    split = allocate(D(1000), {"a": D(0), "b": D(100)}, {"a": D(1), "b": D(1)})
    # yeni toplam 1100 → hedef 550/550; açık a=550, b=450; tam kapanır
    assert split == {"a": D(550), "b": D(450)}
    assert allocate(D(0), {"a": D(1)}, {"a": D(1)}) == {"a": D(0)}
    assert allocate(D(10), {"a": D(1)}, {"a": D(0)}) == {"a": D(0)}


def test_apply_shocks() -> None:
    rows = [(D(100), "BIST_STOCK", False), (D(100), "US_STOCK", True)]
    total = apply_shocks(rows, {"BIST_STOCK": D("-0.2")}, D("0.1"))
    assert total == D(80) + D(110)


# --------------------------------------------------------------------------- puan
def test_linear_score_directions() -> None:
    assert linear_score(D(10), D(25), D(50), D(20)) == D(20)  # küçük iyi
    assert linear_score(D(50), D(25), D(50), D(20)) == D(0)
    assert linear_score(D("37.5"), D(25), D(50), D(20)) == D("10.0")
    assert linear_score(D(5), D(0), D(-20), D(20)) == D(20)  # büyük iyi
    assert linear_score(D(-10), D(0), D(-20), D(20)) == D("10.0")


def test_total_score_ignores_missing_components() -> None:
    comps = [
        ScoreComponent("a", "A", D(10), D(20), ""),
        ScoreComponent("b", "B", D(0), D(30), "", available=False),
    ]
    assert total_score(comps) == D(50)
    assert comps[1].missing == 0 and comps[0].missing == D(10)
    assert total_score([]) is None
    assert score_grade(None) == "Veri yok"
    assert score_grade(D(90)) == "Çok iyi"
    assert score_grade(D(40)) == "Dikkat"
