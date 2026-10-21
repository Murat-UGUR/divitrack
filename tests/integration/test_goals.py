from datetime import date
from decimal import Decimal as D

import pytest

from divitrack.core.entities import Asset, CashFlow, Goal, Transaction
from divitrack.core.enums import AssetClass, Currency, TxType
from divitrack.core.exceptions import DuplicateError, NotFoundError, ValidationError
from divitrack.services.budget_service import add_months, month_start
from divitrack.services.goal_service import GoalProgress, months_between

TODAY = date.today()


def test_months_between() -> None:
    assert months_between(date(2026, 10, 21), date(2027, 10, 1)) == 12
    assert months_between(date(2026, 10, 21), date(2026, 1, 1)) == 0


def test_goal_validation() -> None:
    with pytest.raises(ValidationError):
        Goal(" ", D(1), TODAY)
    with pytest.raises(ValidationError):
        Goal("Ev", D(0), TODAY)
    with pytest.raises(ValidationError):
        Goal("Ev", D(1), TODAY, extra_saved=D(-1))
    assert Goal("Ev", D(1), TODAY, asset_ids=(3, 1, 3)).asset_ids == (1, 3)


def test_progress_status_math() -> None:
    goal = Goal("Ev", D(1000), TODAY)
    behind = GoalProgress(goal, D(400), 6, D(50), ())
    assert behind.remaining == D(600) and behind.required_monthly == D(100)
    assert behind.projected == D(700) and behind.status == "Geride" and behind.months_needed == 12
    assert GoalProgress(goal, D(400), 6, D(100), ()).status == "Yolunda"
    assert GoalProgress(goal, D(1200), 0, None, ()).status == "Ulaşıldı"
    assert GoalProgress(goal, D(1200), 0, None, ()).pct == 100
    assert GoalProgress(goal, D(10), 0, None, ()).status == "Süre doldu"
    assert GoalProgress(goal, D(10), 3, None, ()).status == "Tasarruf verisi yok"
    assert GoalProgress(goal, D(10), 3, D(0), ()).months_needed is None


@pytest.fixture
def seeded(container):
    c = container
    thy = c.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    cash = c.portfolio.add_asset(Asset("NAKIT", AssetClass.CASH, Currency.TRY))
    for a, q, p in ((thy, "10", "250"), (cash, "5000", "1")):
        c.portfolio.add_transaction(
            Transaction(asset_id=a.id, tx_type=TxType.BUY, quantity=D(q), price=D(p), trade_date=date(2024, 1, 2))
        )
    salary = c.budget.categories.find("Maaş")
    rent = c.budget.categories.find("Kira / aidat")
    last = add_months(month_start(TODAY), -1)
    c.budget.add_flow(CashFlow(salary.id, last, D("30000")))
    c.budget.add_flow(CashFlow(rent.id, last, D("20000")))
    return c, thy, cash


def test_goal_crud_and_progress(seeded) -> None:
    c, thy, cash = seeded
    target = add_months(month_start(TODAY), 10)
    goal = c.goals.add(Goal("Acil durum fonu", D("150000"), target, D("1000"), (thy.id, cash.id)))
    with pytest.raises(DuplicateError):
        c.goals.add(Goal("acil durum fonu", D(1), target))
    with pytest.raises(NotFoundError):
        c.goals.add(Goal("Araba", D(1), target, asset_ids=(999,)))

    assert c.goals.monthly_pace() == D("10000")
    gp = c.goals.progress()[0]
    assert gp.saved == D("3000") + D("5000") + D("1000")  # THYAO 10×300 + nakit + ek
    # 9.000 birikmiş + 10 ay × 10.000 = 109.000 < 150.000 → geride; ayda 14.100 gerekiyor
    assert gp.months_left == 10 and gp.status == "Geride" and gp.required_monthly == D("14100")
    assert gp.symbols == ("THYAO", "NAKIT")
    texts = [a.text for a in c.insights.alerts(c.portfolio.summary(Currency.TRY))]
    assert any("hedefi geride: ayda 14100 TL gerekiyor" in t for t in texts)

    # Bir varlık tek hedefe bağlanır: ikinci hedefe verilince birincisinden çıkar
    car = c.goals.add(Goal("Araba", D("50000"), target, asset_ids=(cash.id,)))
    by_name = {g.name: g for g in c.goals.list()}
    assert by_name["Acil durum fonu"].asset_ids == (thy.id,)
    assert by_name["Araba"].asset_ids == (cash.id,)

    car.target_amount = D("5000")  # bağlı nakit 5.000 TL
    c.goals.update(car)
    assert next(p for p in c.goals.progress() if p.goal.name == "Araba").status == "Ulaşıldı"
    c.goals.delete(car.id)
    assert [g.name for g in c.goals.list()] == ["Acil durum fonu"]
    assert goal.id is not None


def test_goals_in_json_roundtrip(seeded, tmp_path) -> None:
    from divitrack.bootstrap import build_container
    from divitrack.config import Settings

    c, thy, _ = seeded
    c.goals.add(Goal("Ev", D("500000"), date(2030, 1, 1), D("10"), (thy.id,)))
    other = build_container(Settings(db_path=tmp_path / "g.db"), use_default_provider=False)
    payload = c.transfer.export_json()
    other.transfer.import_json(payload)
    other.transfer.import_json(payload)  # ikinci kez eklenmez
    goals = other.goals.list()
    assert [(g.name, g.target_amount, g.extra_saved) for g in goals] == [("Ev", D("500000"), D("10"))]
    assert [other.portfolio.assets.get(a).symbol for a in goals[0].asset_ids] == ["THYAO"]


def test_fund_name_filled_from_provider(container, provider) -> None:
    provider.describe = lambda symbol: "İŞ PORTFÖY  TEKNOLOJİ FONU" if symbol == "TEFAS:TTE" else None
    fund = container.portfolio.add_asset(Asset("TTE", AssetClass.FUND, Currency.TRY))
    assert fund.name == "İŞ PORTFÖY  TEKNOLOJİ FONU"
    named = container.portfolio.add_asset(Asset("AFT", AssetClass.FUND, Currency.TRY, name="Kendi adım"))
    assert named.name == "Kendi adım"

    def boom(symbol):
        raise ConnectionError("yok")

    provider.describe = boom
    assert container.portfolio.add_asset(Asset("XYZ", AssetClass.FUND, Currency.TRY)).name == ""
