from datetime import date
from decimal import Decimal as D

import pytest

from divitrack.core.entities import Transaction
from divitrack.core.enums import TxType
from divitrack.core.returns import (
    average_after_buy,
    open_lots,
    quantity_for_target_average,
    real_rate,
    required_change,
    xirr,
)


def tx(t, qty, price, d, fee="0", id_=None):
    return Transaction(asset_id=1, tx_type=t, quantity=D(qty), price=D(price), trade_date=d, fee=D(fee), id=id_)


def test_fifo_sell_consumes_oldest_lot_first() -> None:
    lots = open_lots(
        [
            tx(TxType.BUY, "10", "100", date(2024, 1, 1), fee="10"),
            tx(TxType.BUY, "10", "200", date(2024, 6, 1)),
            tx(TxType.SELL, "15", "300", date(2025, 1, 1)),
        ]
    )
    assert len(lots) == 1
    assert lots[0].trade.trade_date == date(2024, 6, 1)
    assert lots[0].quantity == D("5")
    assert lots[0].cost == D("1000")  # 5 × 200


def test_partial_sell_keeps_cost_proportional_including_fee() -> None:
    lots = open_lots(
        [tx(TxType.BUY, "10", "100", date(2024, 1, 1), fee="20"), tx(TxType.SELL, "4", "150", date(2024, 2, 1))]
    )
    assert lots[0].quantity == D("6")
    assert lots[0].cost == D("612")  # (1000 + 20) × 6/10


def test_bonus_scales_all_open_lots_without_changing_cost() -> None:
    lots = open_lots(
        [
            tx(TxType.BUY, "10", "100", date(2024, 1, 1)),
            tx(TxType.BUY, "30", "100", date(2024, 2, 1)),
            tx(TxType.BONUS, "40", "0", date(2024, 3, 1)),  # %100 bedelsiz
        ]
    )
    assert [lot.quantity for lot in lots] == [D("20"), D("60")]
    assert [lot.cost for lot in lots] == [D("1000"), D("3000")]


def test_reinvest_opens_a_lot() -> None:
    lots = open_lots([tx(TxType.REINVEST, "2", "50", date(2024, 1, 1))])
    assert lots[0].trade.tx_type is TxType.REINVEST
    assert lots[0].cost == D("100")


def test_xirr_simple_one_year_doubling() -> None:
    rate = xirr([(date(2024, 1, 1), D("-100")), (date(2025, 1, 1), D("200"))])
    assert rate is not None
    assert abs(rate - D("1.0")) < D("0.01")  # 366 gün → ~%99,8


def test_xirr_matches_known_value_with_two_deposits() -> None:
    flows = [(date(2024, 1, 1), D("-1000")), (date(2024, 7, 1), D("-1000")), (date(2025, 1, 1), D("2300"))]
    rate = xirr(flows)
    assert rate is not None
    # gerçek gün sayısıyla (366 ve 184 gün, /365): 1000·(1+r)^1.0027 + 1000·(1+r)^0.5041 = 2300 → r ≈ 0.20214
    assert abs(rate - D("0.20214")) < D("0.0005")


@pytest.mark.parametrize(
    "flows",
    [
        [],
        [(date(2024, 1, 1), D("-100"))],
        [(date(2024, 1, 1), D("100")), (date(2025, 1, 1), D("50"))],
        [(date(2024, 1, 1), D("-100")), (date(2024, 1, 1), D("120"))],
    ],
)
def test_xirr_returns_none_when_undefined(flows) -> None:
    assert xirr(flows) is None


def test_xirr_handles_losses() -> None:
    rate = xirr([(date(2024, 1, 1), D("-100")), (date(2025, 1, 1), D("50"))])
    assert rate is not None and D("-0.51") < rate < D("-0.49")


def test_real_rate() -> None:
    assert abs(real_rate(D("0.40"), D("0.30")) - D("0.0769")) < D("0.0001")
    assert real_rate(D("0.30"), D("0.30")) == 0


# --------------------------------------------------------------------------- ortalama maliyet ve başa baş
def test_average_after_buy_lowers_average_when_buying_cheaper() -> None:
    change = average_after_buy(D("10"), D("70000"), D("10"), D("5000"))  # ort. 7.000, 5.000'den 10 adet
    assert change.average_before == D("7000")
    assert change.average_after == D("6000")
    assert change.quantity_after == D("20")
    assert change.purchase_amount == D("50000")
    assert abs(change.change_pct - D("-14.2857")) < D("0.001")


def test_average_after_buy_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        average_after_buy(D("0"), D("100"), D("1"), D("1"))
    with pytest.raises(ValueError):
        average_after_buy(D("1"), D("100"), D("-1"), D("1"))


def test_quantity_for_target_average() -> None:
    # 10 adet × 7.000; 5.000'den kaç adetle ortalama 6.000 olur → 10
    assert quantity_for_target_average(D("10"), D("70000"), D("5000"), D("6000")) == D("10")
    assert quantity_for_target_average(D("10"), D("70000"), D("5000"), D("7500")) == 0  # zaten altında
    assert quantity_for_target_average(D("10"), D("70000"), D("6500"), D("6000")) is None  # fiyat hedefin üstünde


def test_required_change() -> None:
    assert required_change(D("120"), D("100")) == D("20")
    assert required_change(D("80"), D("100")) == D("-20")
    assert required_change(None, D("100")) is None
    assert required_change(D("80"), D("0")) is None
