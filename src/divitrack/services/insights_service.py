"""Analiz servisi: amaç dağılımı, enflasyona göre gerçek getiri, sağlık puanı, uyarılar,
aylık yatırım planı, senaryo ve yurt dışı temettü beyan sınırı.

Bu servis veri yazmaz (kurallar hariç); diğer servislerin ürettiği verileri yorumlar.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from divitrack.core.entities import Asset, Transaction
from divitrack.core.enums import AssetClass, Currency, DividendStatus, Purpose, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.core.inflation import inflation_factor, latest_month, month_key
from divitrack.core.money import HUNDRED, ONE, ZERO, pct
from divitrack.core.planning import (
    ScoreComponent,
    allocate,
    apply_shocks,
    linear_score,
    total_score,
    weights,
)
from divitrack.repositories.sqlite import (
    CpiRepository,
    DividendRepository,
    ScoreHistoryRepository,
    SettingsRepository,
    TransactionRepository,
)
from divitrack.services.budget_service import BudgetService, add_months
from divitrack.services.market.market_data_service import MarketDataService, value_on
from divitrack.services.portfolio_service import PortfolioService, PortfolioSummary

# --------------------------------------------------------------------------- kurallar
DEFAULT_TARGETS: dict[Purpose, Decimal] = {
    Purpose.DEFENSIVE: Decimal("0.30"),
    Purpose.GROWTH: Decimal("0.35"),
    Purpose.DIVIDEND: Decimal("0.25"),
    Purpose.EXPERIMENT: Decimal("0.10"),
}
TARGET_KEY = "target.{}"
SINGLE_MAX_KEY = "rule.single_position_max"
DRIFT_TOL_KEY = "rule.drift_tolerance"
DECLARATION_LIMIT_KEY = "tax.declaration_limit"


@dataclass(slots=True)
class PlanRules:
    """Kullanıcının kendi kuralları. Oranlar 0–1, beyan sınırı TL.

    Deneme hedefi aynı zamanda deneme bütçesidir: üstüne çıkılırsa uyarı verilir.
    Beyan sınırı her yıl değişir; varsayılan değer yoktur, kullanıcı girer.
    """

    targets: dict[Purpose, Decimal] = field(default_factory=lambda: dict(DEFAULT_TARGETS))
    single_position_max: Decimal = Decimal("0.25")
    drift_tolerance: Decimal = Decimal("0.05")
    declaration_limit: Decimal | None = None

    def validate(self) -> None:
        if any(t < 0 for t in self.targets.values()):
            raise ValidationError("Hedef oranlar negatif olamaz")
        total = sum(self.targets.values(), ZERO)
        if abs(total - ONE) > Decimal("0.001"):
            raise ValidationError(f"Hedeflerin toplamı %100 olmalı (şu an %{total * HUNDRED:.1f})")
        if not ZERO < self.single_position_max <= ONE:
            raise ValidationError("Tek kalem sınırı %0–100 arasında olmalı")
        if not ZERO <= self.drift_tolerance < ONE:
            raise ValidationError("Sapma toleransı %0–100 arasında olmalı")
        if self.declaration_limit is not None and self.declaration_limit < 0:
            raise ValidationError("Beyan sınırı negatif olamaz")

    @property
    def experiment_budget(self) -> Decimal:
        return self.targets.get(Purpose.EXPERIMENT, ZERO)


# --------------------------------------------------------------------------- sonuç tipleri
@dataclass(frozen=True, slots=True)
class PurposeRow:
    purpose: Purpose
    value: Decimal
    weight: Decimal  # 0–1
    target: Decimal  # 0–1
    symbols: tuple[str, ...]

    @property
    def drift_pp(self) -> Decimal:
        return (self.weight - self.target) * HUNDRED


@dataclass(frozen=True, slots=True)
class RealReturnRow:
    asset: Asset
    is_open: bool
    invested: Decimal  # net yatırılan (TL, işlem günü kuruyla)
    invested_real: Decimal | None  # enflasyonla bugüne taşınmış hali
    value: Decimal  # güncel değer (TL)
    approx_fx: bool  # işlem günü kuru bulunamadı, güncel kur kullanıldı

    @property
    def nominal_gain(self) -> Decimal:
        return self.value - self.invested

    @property
    def nominal_pct(self) -> Decimal | None:
        return pct(self.nominal_gain, self.invested) if self.invested > 0 else None

    @property
    def real_gain(self) -> Decimal | None:
        return None if self.invested_real is None else self.value - self.invested_real

    @property
    def real_pct(self) -> Decimal | None:
        if self.invested_real is None or self.invested_real <= 0:
            return None
        return pct(self.value - self.invested_real, self.invested_real)


@dataclass(slots=True)
class RealReturnReport:
    rows: list[RealReturnRow]
    cpi_month: str | None
    unpriced: list[str] = field(default_factory=list)

    @property
    def has_cpi(self) -> bool:
        return self.cpi_month is not None

    @property
    def invested(self) -> Decimal:
        return sum((r.invested for r in self.rows), ZERO)

    @property
    def invested_real(self) -> Decimal | None:
        vals = [r.invested_real for r in self.rows]
        if not vals or any(v is None for v in vals):
            return None
        return sum((v for v in vals if v is not None), ZERO)

    @property
    def value(self) -> Decimal:
        return sum((r.value for r in self.rows), ZERO)

    @property
    def real_pct(self) -> Decimal | None:
        real = self.invested_real
        return None if real is None or real <= 0 else pct(self.value - real, real)

    @property
    def nominal_pct(self) -> Decimal | None:
        return pct(self.value - self.invested, self.invested) if self.invested > 0 else None


@dataclass(frozen=True, slots=True)
class Alert:
    level: str  # "error" | "warning" | "info"
    text: str


@dataclass(frozen=True, slots=True)
class PlanRow:
    purpose: Purpose
    current: Decimal
    target: Decimal
    amount: Decimal
    weight_before: Decimal
    weight_after: Decimal
    symbols: tuple[str, ...]


@dataclass(slots=True)
class DeclarationStatus:
    year: int
    gross_try: Decimal
    limit: Decimal | None
    items: list[tuple[date, str, Decimal]]  # (ödeme tarihi, sembol, brüt TL)
    approx_fx: bool = False

    @property
    def ratio(self) -> Decimal | None:
        return None if not self.limit else self.gross_try / self.limit

    @property
    def exceeded(self) -> bool:
        return self.limit is not None and self.gross_try > self.limit


@dataclass(slots=True)
class Health:
    components: list[ScoreComponent]

    @property
    def score(self) -> Decimal | None:
        return total_score(self.components)

    @property
    def potential(self) -> list[ScoreComponent]:
        """En çok puan kazandırabilecek bileşenler (önce en büyük kayıp)."""
        return sorted((c for c in self.components if c.missing > 0), key=lambda c: -c.missing)


@dataclass(frozen=True, slots=True)
class AssetCard:
    """Varlık karnesi: tek bakışta bir kalemin portföydeki durumu ve işaretleri."""

    asset: Asset
    weight: Decimal | None  # 0–1
    nominal_pct: Decimal | None
    real_pct: Decimal | None
    dividend_yield: Decimal | None  # %
    flags: tuple[str, ...]

    @property
    def grade(self) -> str:
        if not self.flags:
            return "İyi"
        return "Dikkat" if len(self.flags) >= 2 else "İzle"


# --------------------------------------------------------------------------- servis
class InsightsService:
    def __init__(
        self,
        portfolio: PortfolioService,
        transactions: TransactionRepository,
        dividends: DividendRepository,
        budget: BudgetService,
        cpi: CpiRepository,
        market: MarketDataService,
        settings: SettingsRepository,
        scores: ScoreHistoryRepository,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.portfolio = portfolio
        self.transactions = transactions
        self.dividends = dividends
        self.budget = budget
        self.cpi = cpi
        self.market = market
        self.settings = settings
        self.scores = scores
        self.goals: Any = None  # GoalService; bootstrap bağlar (döngüsel bağımlılığı önlemek için sonradan)
        self.today = today

    # ------------------------------------------------------------------ kurallar
    def rules(self) -> PlanRules:
        def dec(key: str, default: Decimal | None) -> Decimal | None:
            raw = self.settings.get(key)
            return Decimal(raw) if raw not in (None, "") else default

        targets = {p: dec(TARGET_KEY.format(p.value), DEFAULT_TARGETS[p]) or ZERO for p in Purpose.targeted()}
        return PlanRules(
            targets=targets,
            single_position_max=dec(SINGLE_MAX_KEY, Decimal("0.25")) or Decimal("0.25"),
            drift_tolerance=dec(DRIFT_TOL_KEY, Decimal("0.05")) or ZERO,
            declaration_limit=dec(DECLARATION_LIMIT_KEY, None),
        )

    def save_rules(self, rules: PlanRules) -> None:
        rules.validate()
        for p, t in rules.targets.items():
            self.settings.set(TARGET_KEY.format(p.value), str(t))
        self.settings.set(SINGLE_MAX_KEY, str(rules.single_position_max))
        self.settings.set(DRIFT_TOL_KEY, str(rules.drift_tolerance))
        self.settings.set(
            DECLARATION_LIMIT_KEY, "" if rules.declaration_limit is None else str(rules.declaration_limit)
        )

    # ------------------------------------------------------------------ dağılım
    def purpose_allocation(self, summary: PortfolioSummary, rules: PlanRules | None = None) -> list[PurposeRow]:
        rules = rules or self.rules()
        values: dict[Purpose, Decimal] = defaultdict(lambda: ZERO)
        symbols: dict[Purpose, list[str]] = defaultdict(list)
        for pv in summary.open_positions:
            if pv.market_value_base is None:
                continue
            values[pv.asset.purpose] += pv.market_value_base
            symbols[pv.asset.purpose].append(pv.asset.symbol)
        w = weights(dict(values))
        purposes = [*Purpose.targeted(), *([Purpose.UNASSIGNED] if values.get(Purpose.UNASSIGNED) else [])]
        return [
            PurposeRow(
                p,
                values.get(p, ZERO),
                w.get(p, ZERO),
                rules.targets.get(p, ZERO),
                tuple(sorted(symbols.get(p, []))),
            )
            for p in purposes
        ]

    # ------------------------------------------------------------------ enflasyon
    def real_returns(self, *, force_refresh: bool = False) -> RealReturnReport:
        """Her varlık için: TL olarak net yatırılan, bunun bugünkü (TÜFE'ye göre) karşılığı ve güncel değer.

        Yeni para yalnızca alış ve bedelli işlemleridir; satışlar yatırılandan düşülür.
        Bedelsiz ve temettüden alış yeni para sayılmaz. Yabancı para işlemleri işlem günü kuruyla
        TL'ye çevrilir; kur geçmişi alınamazsa güncel kur kullanılır ve satır işaretlenir.
        """
        summary = self.portfolio.summary(Currency.TRY, force_refresh=force_refresh)
        index = self.cpi.all()
        grouped = self.transactions.by_asset()
        all_txs = [t for txs in grouped.values() for t in txs]
        foreign = {pv.asset.currency for pv in summary.positions if pv.asset.currency is not Currency.TRY}
        # hafta sonu/tatil boşluğu için birkaç gün öncesinden başla
        start = min((t.trade_date for t in all_txs), default=self.today()) - timedelta(days=7)
        fx_hist = self.market.fx_history(foreign, Currency.TRY, start) if foreign else {}
        current_fx = self.market.fx_rates(foreign, Currency.TRY) if foreign else {}

        rows: list[RealReturnRow] = []
        unpriced: list[str] = []
        for pv in summary.positions:
            txs = grouped.get(pv.asset.id or -1, [])
            if not txs:
                continue
            if pv.state.is_open and pv.market_value_base is None:
                unpriced.append(pv.asset.symbol)
                continue
            invested, invested_real, approx = self._cash_in(txs, pv.asset.currency, index, fx_hist, current_fx)
            value = (pv.market_value_base or ZERO) if pv.state.is_open else ZERO
            rows.append(RealReturnRow(pv.asset, pv.state.is_open, invested, invested_real, value, approx))
        rows.sort(key=lambda r: (not r.is_open, -r.value))
        return RealReturnReport(rows, latest_month(index), unpriced)

    def _cash_in(
        self,
        txs: list[Transaction],
        currency: Currency,
        index: dict[str, Decimal],
        fx_hist: dict[Currency, dict[date, Decimal]],
        current_fx: dict[Currency, Decimal],
    ) -> tuple[Decimal, Decimal | None, bool]:
        invested = ZERO
        invested_real: Decimal | None = ZERO
        approx = False
        for t in txs:
            if t.tx_type.is_new_money:
                amount = t.gross_amount + t.fee
            elif t.tx_type is TxType.SELL:
                amount = -(t.gross_amount - t.fee)
            else:
                continue
            if currency is not Currency.TRY:
                rate = value_on(fx_hist.get(currency, {}), t.trade_date)
                if rate is None:
                    rate = current_fx.get(currency)
                    approx = True
                if rate is None:
                    return invested, None, True
                amount *= rate
            invested += amount
            factor = inflation_factor(index, t.trade_date) if index else None
            if factor is None or invested_real is None:
                invested_real = None
            else:
                invested_real += amount * factor
        return invested, invested_real, approx

    # ------------------------------------------------------------------ vergi
    def declaration_status(self, year: int | None = None) -> DeclarationStatus:
        """Yurt dışı (TL dışı) ödenmiş temettülerin yıllık brüt TL toplamı ve beyan sınırına oranı.

        Çevrim ödeme günü kuruyla yapılır (alınamazsa güncel kur). Resmi hesap için TCMB döviz
        alış kuru kullanılır; buradaki tutar bir ön izlemedir.
        """
        year = year or self.today().year
        assets = {a.id: a for a in self.portfolio.assets.list()}
        paid = [
            d
            for d in self.dividends.list()
            if d.status is DividendStatus.PAID and d.pay_date.year == year and d.currency is not Currency.TRY
        ]
        currencies = {d.currency for d in paid}
        hist = (
            self.market.fx_history(currencies, Currency.TRY, date(year, 1, 1) - timedelta(days=7)) if currencies else {}
        )
        current = self.market.fx_rates(currencies, Currency.TRY) if currencies else {}
        items: list[tuple[date, str, Decimal]] = []
        approx = False
        for d in sorted(paid, key=lambda d: d.pay_date):
            rate = value_on(hist.get(d.currency, {}), d.pay_date)
            if rate is None:
                rate = current.get(d.currency)
                approx = True
            if rate is None:
                continue
            asset = assets.get(d.asset_id)
            items.append((d.pay_date, asset.symbol if asset else "?", d.gross * rate))
        total = sum((x[2] for x in items), ZERO)
        return DeclarationStatus(year, total, self.rules().declaration_limit, items, approx)

    # ------------------------------------------------------------------ plan
    def monthly_plan(self, amount: Decimal, summary: PortfolioSummary, rules: PlanRules | None = None) -> list[PlanRow]:
        """Yeni parayı amaçlara böler (satış önermez). Tutar raporlama para birimindedir."""
        rules = rules or self.rules()
        rows = {r.purpose: r for r in self.purpose_allocation(summary, rules)}
        targeted = Purpose.targeted()
        values = {p: rows[p].value if p in rows else ZERO for p in targeted}
        split = allocate(amount, values, {p: rules.targets.get(p, ZERO) for p in targeted})
        unassigned = rows[Purpose.UNASSIGNED].value if Purpose.UNASSIGNED in rows else ZERO
        before_total = sum(values.values(), ZERO) + unassigned
        after_total = before_total + amount
        return [
            PlanRow(
                p,
                values[p],
                rules.targets.get(p, ZERO),
                split.get(p, ZERO),
                values[p] / before_total if before_total > 0 else ZERO,
                (values[p] + split.get(p, ZERO)) / after_total if after_total > 0 else ZERO,
                rows[p].symbols if p in rows else (),
            )
            for p in targeted
        ]

    # ------------------------------------------------------------------ senaryo
    @staticmethod
    def scenario(summary: PortfolioSummary, class_shocks: dict[AssetClass, Decimal], fx_shock: Decimal) -> Decimal:
        """Fiyat ve kur şokları sonrası portföy değeri (raporlama para biriminde).

        Kur şoku, raporlama para biriminden farklı para birimindeki varlıklara uygulanır
        (ör. TL raporlamada +%10 = döviz %10 değer kazanır).
        """
        rows = [
            (pv.market_value_base, pv.asset.asset_class.value, pv.asset.currency is not summary.base_currency)
            for pv in summary.open_positions
            if pv.market_value_base is not None
        ]
        shocks = {k.value: v for k, v in class_shocks.items()}
        return apply_shocks([(v, c, f) for v, c, f in rows if v is not None], shocks, fx_shock)

    # ------------------------------------------------------------------ sağlık
    def health(
        self,
        summary: PortfolioSummary,
        real: RealReturnReport | None = None,
        rules: PlanRules | None = None,
    ) -> Health:
        rules = rules or self.rules()
        comps: list[ScoreComponent] = []
        priced = [pv for pv in summary.open_positions if pv.market_value_base is not None]
        has_portfolio = bool(priced) and summary.total_value > 0
        allocation = self.purpose_allocation(summary, rules) if has_portfolio else []

        # 1) Amaç dağılımı (25)
        tol = rules.drift_tolerance * HUNDRED
        excess = sum(
            (max(ZERO, abs(r.drift_pp) - tol) for r in allocation if r.purpose is not Purpose.UNASSIGNED), ZERO
        )
        unassigned = next((r.weight * HUNDRED for r in allocation if r.purpose is Purpose.UNASSIGNED), ZERO)
        excess += unassigned
        worst = max(allocation, key=lambda r: abs(r.drift_pp), default=None)
        comps.append(
            ScoreComponent(
                "allocation",
                "Amaç dağılımı hedefe uygun mu",
                linear_score(excess, ZERO, Decimal(30), Decimal(25)) if has_portfolio else ZERO,
                Decimal(25),
                f"Tolerans dışı toplam sapma {excess:.1f} puan"
                + (f"; atanmamış pay %{unassigned:.1f}" if unassigned else ""),
                f"En büyük sapma {worst.purpose.label} ({worst.drift_pp:+.1f} puan): yeni parayı buna göre yönlendirin"
                if worst is not None and abs(worst.drift_pp) > tol
                else "",
                available=has_portfolio,
            )
        )

        # 2) Tek kalem yoğunlaşması (20)
        top = max(priced, key=lambda pv: pv.market_value_base or ZERO, default=None)
        top_w = (top.market_value_base or ZERO) / summary.total_value * HUNDRED if top and has_portfolio else ZERO
        limit = rules.single_position_max * HUNDRED
        comps.append(
            ScoreComponent(
                "concentration",
                "Tek kaleme bağımlılık",
                linear_score(top_w, limit, limit * 2, Decimal(20)) if has_portfolio else ZERO,
                Decimal(20),
                f"En büyük kalem {top.asset.symbol}: %{top_w:.1f} (sınır %{limit:.0f})" if top else "Pozisyon yok",
                f"{top.asset.symbol} payını %{limit:.0f} altına indirmek için yeni alımları diğer kalemlere yapın"
                if top and top_w > limit
                else "",
                available=has_portfolio,
            )
        )

        # 3) Deneme bütçesi (10)
        exp_w = next((r.weight * HUNDRED for r in allocation if r.purpose is Purpose.EXPERIMENT), ZERO)
        budget = rules.experiment_budget * HUNDRED
        comps.append(
            ScoreComponent(
                "experiment",
                "Deneme bütçesine uyum",
                linear_score(exp_w, budget, max(budget * 2, budget + 5), Decimal(10)) if has_portfolio else ZERO,
                Decimal(10),
                f"Deneme payı %{exp_w:.1f} (bütçe %{budget:.0f})",
                "Deneme kalemlerine yeni para eklemeyin; kâr alıp ana amaçlara aktarın" if exp_w > budget else "",
                available=has_portfolio,
            )
        )

        # 4) Enflasyona karşı (20)
        real_pct = real.real_pct if real is not None else None
        comps.append(
            ScoreComponent(
                "inflation",
                "Enflasyonun üstünde mi",
                linear_score(real_pct, ZERO, Decimal(-20), Decimal(20)) if real_pct is not None else ZERO,
                Decimal(20),
                f"Reel getiri %{real_pct:.1f} (TÜFE {real.cpi_month})"
                if real_pct is not None and real
                else "TÜFE verisi yok",
                "Reel getirisi negatif kalemleri Analiz sayfasında inceleyin"
                if real_pct is not None and real_pct < 0
                else ("Ayarlar > TÜFE bölümüne aylık endeksi girin" if real_pct is None else ""),
                available=real_pct is not None and has_portfolio,
            )
        )

        # 5) Çeşitlendirme (10)
        classes = {pv.asset.asset_class for pv in priced}
        currencies = {pv.asset.currency for pv in priced}
        div_points = (Decimal(min(len(classes), 4)) / 4 * 7 + (Decimal(3) if len(currencies) > 1 else ZERO)).quantize(
            Decimal("0.1")
        )
        comps.append(
            ScoreComponent(
                "diversification",
                "Sınıf ve para birimi çeşitliliği",
                div_points if has_portfolio else ZERO,
                Decimal(10),
                f"{len(classes)} varlık sınıfı, {len(currencies)} para birimi",
                "Tek para birimine bağlı kalmamak için döviz/altın payı düşünün" if len(currencies) <= 1 else "",
                available=has_portfolio,
            )
        )

        # 6) Tasarruf oranı (15)
        rate = self.budget.average_savings_rate(3)
        target = self.budget.savings_target * HUNDRED
        comps.append(
            ScoreComponent(
                "savings",
                "Gelirin ne kadarı artıyor",
                linear_score(rate, target, ZERO, Decimal(15)) if rate is not None else ZERO,
                Decimal(15),
                f"Son 3 ay tasarruf oranı %{rate:.1f} (hedef %{target:.0f})" if rate is not None else "Bütçe kaydı yok",
                "Bütçesini aşan kategorileri Bütçe sayfasında inceleyin"
                if rate is not None and rate < target
                else ("Bütçe sayfasına gelir ve giderlerinizi girin" if rate is None else ""),
                available=rate is not None,
            )
        )
        return Health(comps)

    # ------------------------------------------------------------------ puan geçmişi
    def record_score(self, health: Health, *, day: date | None = None) -> None:
        """Ayın puanını kaydeder (aynı ay içinde son değer kalır)."""
        score = health.score
        if score is None:
            return
        parts = {c.key: f"{c.points}/{c.max_points}" for c in health.components if c.available}
        self.scores.upsert(month_key(day or self.today()), score, parts)

    def score_history(self) -> list[tuple[str, Decimal]]:
        return [(m, s) for m, s, _ in self.scores.list()]

    # ------------------------------------------------------------------ varlık karnesi
    def report_cards(
        self,
        summary: PortfolioSummary,
        real: RealReturnReport | None = None,
        yields: dict[int, Decimal | None] | None = None,
        rules: PlanRules | None = None,
    ) -> list[AssetCard]:
        rules = rules or self.rules()
        real_rows = {r.asset.id: r for r in real.rows} if real else {}
        yields = yields or {}
        cards: list[AssetCard] = []
        for pv in summary.open_positions:
            aid = pv.asset.id
            weight = (
                pv.market_value_base / summary.total_value
                if pv.market_value_base is not None and summary.total_value > 0
                else None
            )
            rr = real_rows.get(aid)
            flags: list[str] = []
            if pv.market_value_base is None:
                flags.append("Fiyat yok")
            if weight is not None and weight > rules.single_position_max:
                flags.append("Tek kalem sınırı aşıldı")
            if pv.asset.purpose is Purpose.UNASSIGNED:
                flags.append("Amaç atanmamış")
            if rr is not None and rr.real_pct is not None and rr.real_pct < 0:
                flags.append("Enflasyonun gerisinde")
            dy = yields.get(aid or -1)
            if pv.asset.purpose is Purpose.DIVIDEND and (dy is None or dy <= 0):
                flags.append("Temettü amacında ama temettü yok")
            cards.append(
                AssetCard(
                    pv.asset,
                    weight,
                    pv.unrealized_pct,
                    rr.real_pct if rr else None,
                    dy,
                    tuple(flags),
                )
            )
        return sorted(cards, key=lambda c: (-len(c.flags), -(c.weight or ZERO)))

    # ------------------------------------------------------------------ uyarılar
    def alerts(
        self,
        summary: PortfolioSummary,
        real: RealReturnReport | None = None,
        rules: PlanRules | None = None,
    ) -> list[Alert]:
        rules = rules or self.rules()
        out: list[Alert] = []
        today = self.today()
        if summary.unpriced:
            out.append(
                Alert("warning", "Fiyatı alınamayan varlıklar: " + ", ".join(a.symbol for a in summary.unpriced))
            )
        if summary.stale:
            out.append(Alert("info", "Bazı fiyatlar önbellekten gösteriliyor (güncellenemedi)."))

        if summary.total_value > 0:
            tol = rules.drift_tolerance * HUNDRED
            for r in self.purpose_allocation(summary, rules):
                if r.purpose is Purpose.UNASSIGNED:
                    out.append(
                        Alert(
                            "warning", f"Amacı atanmamış varlıklar (%{r.weight * HUNDRED:.1f}): {', '.join(r.symbols)}"
                        )
                    )
                elif abs(r.drift_pp) > tol:
                    side = "üstünde" if r.drift_pp > 0 else "altında"
                    out.append(
                        Alert(
                            "warning",
                            f"{r.purpose.label} payı %{r.weight * HUNDRED:.1f}; hedef %{r.target * HUNDRED:.0f}'in "
                            f"{abs(r.drift_pp):.1f} puan {side}.",
                        )
                    )
            limit = rules.single_position_max
            for pv in summary.open_positions:
                if pv.market_value_base is None:
                    continue
                w = pv.market_value_base / summary.total_value
                if w > limit:
                    out.append(
                        Alert(
                            "warning",
                            f"{pv.asset.symbol} portföyün %{w * HUNDRED:.1f}'i "
                            f"(tek kalem sınırı %{limit * HUNDRED:.0f}).",
                        )
                    )

        if real is not None:
            if not real.has_cpi:
                out.append(Alert("info", "TÜFE verisi girilmemiş; reel getiri hesaplanamıyor (Ayarlar > TÜFE)."))
            else:
                assert real.cpi_month is not None
                last = date.fromisoformat(real.cpi_month + "-01")
                if add_months(last, 2) <= today.replace(day=1):
                    out.append(Alert("info", f"TÜFE verisi {real.cpi_month} ayında kalmış; son ayları ekleyin."))
                losers = [r.asset.symbol for r in real.rows if r.is_open and (r.real_pct or ZERO) < -10]
                if losers:
                    out.append(Alert("info", "Enflasyonun %10'dan fazla gerisinde kalanlar: " + ", ".join(losers)))

        decl = self.declaration_status(today.year)
        if decl.limit and decl.ratio is not None:
            if decl.exceeded:
                out.append(
                    Alert("error", f"{decl.year} yurt dışı temettü toplamı beyan sınırını aştı; beyanname gerekebilir.")
                )
            elif decl.ratio >= Decimal("0.8"):
                out.append(
                    Alert(
                        "warning",
                        f"{decl.year} yurt dışı temettü toplamı beyan sınırının "
                        f"%{decl.ratio * HUNDRED:.0f}'ine ulaştı.",
                    )
                )

        month = self.budget.month_summary(today)
        for ln in month.over_budget:
            out.append(
                Alert("warning", f"{ln.category.name} bu ay bütçeyi {ln.actual - (ln.budget or ZERO):.0f} TL aştı.")
            )

        soon = today + timedelta(days=14)
        assets = {a.id: a.symbol for a in self.portfolio.assets.list()}
        upcoming = [
            d for d in self.dividends.list() if d.status is DividendStatus.EXPECTED and today <= d.pay_date <= soon
        ]
        for d in sorted(upcoming, key=lambda d: d.pay_date):
            out.append(
                Alert("info", f"{assets.get(d.asset_id, '?')} temettüsü {d.pay_date:%d.%m.%Y} tarihinde bekleniyor.")
            )
        if self.goals is not None:
            for gp in self.goals.progress():
                if gp.status == "Geride":
                    out.append(
                        Alert(
                            "warning",
                            f"'{gp.goal.name}' hedefi geride: ayda {gp.required_monthly or ZERO:.0f} TL gerekiyor, "
                            f"son aylarda ortalama {gp.pace or ZERO:.0f} TL ayrılabildi.",
                        )
                    )
                elif gp.status == "Süre doldu":
                    out.append(
                        Alert("warning", f"'{gp.goal.name}' hedefinin tarihi geçti; kalan {gp.remaining:.0f} TL.")
                    )
        order = {"error": 0, "warning": 1, "info": 2}
        return sorted(out, key=lambda a: order[a.level])
