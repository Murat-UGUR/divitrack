from datetime import date
from decimal import Decimal as D

import pytest

from divitrack.core.calculations import (
    add_years,
    compute_position,
    payment_frequency,
    project_payments,
    ttm_dividend_per_share,
)
from divitrack.core.entities import Transaction
from divitrack.core.enums import TxType
from divitrack.core.exceptions import InsufficientQuantityError, ValidationError
from divitrack.core.money import to_decimal


def tx(t: TxType, q: str, p: str, d: date, fee: str = "0", i: int = 0) -> Transaction:
    return Transaction(asset_id=1, tx_type=t, quantity=D(q), price=D(p), trade_date=d, fee=D(fee), id=i)


def test_weighted_average_cost_includes_fees() -> None:
    s = compute_position(
        [
            tx(TxType.BUY, "10", "100", date(2024, 1, 1), fee="10"),
            tx(TxType.BUY, "10", "200", date(2024, 2, 1)),
        ]
    )
    assert s.quantity == D("20")
    assert s.cost_basis == D("3010")
    assert s.avg_cost == D("150.5")


def test_sell_realizes_pnl_and_keeps_avg_cost() -> None:
    s = compute_position(
        [
            tx(TxType.BUY, "10", "100", date(2024, 1, 1)),
            tx(TxType.SELL, "4", "150", date(2024, 3, 1), fee="5"),
        ]
    )
    assert s.quantity == D("6")
    assert s.avg_cost == D("100")
    assert s.realized_pnl == D("195")  # 600 - 5 - 400
    assert s.cost_basis == D("600")


def test_full_close_resets_cost() -> None:
    s = compute_position(
        [
            tx(TxType.BUY, "5", "10", date(2024, 1, 1)),
            tx(TxType.SELL, "5", "12", date(2024, 1, 2)),
        ]
    )
    assert not s.is_open and s.avg_cost == 0 and s.cost_basis == 0 and s.realized_pnl == D("10")


def test_oversell_rejected() -> None:
    with pytest.raises(InsufficientQuantityError):
        compute_position([tx(TxType.BUY, "1", "10", date(2024, 1, 2)), tx(TxType.SELL, "2", "10", date(2024, 1, 3))])


def test_as_of_filters_future_trades() -> None:
    txs = [tx(TxType.BUY, "1", "10", date(2024, 1, 1)), tx(TxType.BUY, "1", "10", date(2024, 6, 1))]
    assert compute_position(txs, as_of=date(2024, 3, 1)).quantity == 1


def test_invalid_transaction() -> None:
    with pytest.raises(ValidationError):
        tx(TxType.BUY, "0", "10", date(2024, 1, 1))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.234,56", "1234.56"),
        ("12,5", "12.5"),
        ("7", "7"),
        (0.1, "0.1"),
        ("1,234.56", "1234.56"),
        ("1.234.567", "1234567"),
        ("1,234,567", "1234567"),
    ],
)
def test_to_decimal_turkish_formats(raw: object, expected: str) -> None:
    assert to_decimal(raw) == D(expected)


def test_dividend_projection_quarterly() -> None:
    today = date(2025, 6, 15)
    hist = [
        (date(2024, 3, 10), D("0.5")),
        (date(2024, 9, 10), D("0.5")),
        (date(2024, 12, 10), D("0.6")),
        (date(2025, 3, 10), D("0.6")),
        (date(2025, 6, 10), D("0.6")),
    ]
    assert ttm_dividend_per_share(hist, today) == D("2.3")
    assert payment_frequency(hist, today) == 4
    proj = project_payments(hist, today)
    assert [d for d, _ in proj] == [date(2025, 9, 10), date(2025, 12, 10), date(2026, 3, 10), date(2026, 6, 10)]


def test_add_years_leap_day() -> None:
    assert add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)
