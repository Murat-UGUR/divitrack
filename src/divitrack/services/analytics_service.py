"""Ek analizler: alım bazında getiri (nominal, reel, dolar bazında), aylık para akışı,
para ağırlıklı yıllık getiri ve piyasa araçlarıyla kıyas, amaç bazlı senaryo, alım öncesi kontrol.

Bu servis veri yazmaz (kıyas listesi hariç); işlem defteri, fiyatlar, kurlar ve TÜFE'yi yorumlar.
Tüm tutarlar TL'dir; dolar bazında getiri için işlem günü ve bugünkü USD/TRY kuru kullanılır.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from divitrack.core.entities import Asset
from divitrack.core.enums import AssetClass, Currency, Purpose, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.core.inflation import cpi_at, inflation_factor, latest_month, month_key
from divitrack.core.money import HUNDRED, ONE, ZERO, pct
from divitrack.core.planning import apply_shocks
from divitrack.core.returns import open_lots, real_rate, xirr
from divitrack.repositories.sqlite import CpiRepository, SettingsRepository, TransactionRepository
from divitrack.services.budget_service import add_months, month_start
from divitrack.services.insights_service import InsightsService, PlanRules
from divitrack.services.market.market_data_service import MarketDataService, value_on
from divitrack.services.portfolio_service import PortfolioService, PortfolioSummary

BENCHMARKS_KEY = "benchmarks"


# --------------------------------------------------------------------------- sonuç tipleri
@dataclass(frozen=True, slots=True)
class LotRow:
    """Elde kalan tek bir alım ve bugünkü durumu (TL)."""

    asset: Asset
    trade_date: date
    tx_type: TxType
    quantity: Decimal  # elde kalan adet (bedelsiz dahil)
    cost: Decimal  # kalan adede düşen maliyet, işlem günü kuruyla TL
    cost_real: Decimal | None  # maliyetin TÜFE ile bugüne taşınmış hali
    cost_usd: Decimal | None  # maliyetin işlem günü kuruyla dolar karşılığı
    value: Decimal | None  # güncel değer (TL)
    value_usd: Decimal | None
    approx_fx: bool

    @property
    def nominal_pct(self) -> Decimal | None:
        return None if self.value is None or self.cost <= 0 else pct(self.value - self.cost, self.cost)

    @property
    def real_pct(self) -> Decimal | None:
        if self.value is None or self.cost_real is None or self.cost_real <= 0:
            return None
        return pct(self.value - self.cost_real, self.cost_real)

    @property
    def usd_pct(self) -> Decimal | None:
        if self.value_usd is None or self.cost_usd is None or self.cost_usd <= 0:
            return None
        return pct(self.value_usd - self.cost_usd, self.cost_usd)


@dataclass(frozen=True, slots=True)
class AssetReturn:
    """Bir varlığın elde kalan lotlarının toplamı: nominal, reel ve dolar bazında getiri."""

    asset: Asset
    cost: Decimal
    cost_real: Decimal | None
    cost_usd: Decimal | None
    value: Decimal | None
    value_usd: Decimal | None

    @property
    def nominal_pct(self) -> Decimal | None:
        return None if self.value is None or self.cost <= 0 else pct(self.value - self.cost, self.cost)

    @property
    def real_pct(self) -> Decimal | None:
        if self.value is None or self.cost_real is None or self.cost_real <= 0:
            return None
        return pct(self.value - self.cost_real, self.cost_real)

    @property
    def usd_pct(self) -> Decimal | None:
        if self.value_usd is None or self.cost_usd is None or self.cost_usd <= 0:
            return None
        return pct(self.value_usd - self.cost_usd, self.cost_usd)


@dataclass(slots=True)
class LotReport:
    lots: list[LotRow]
    cpi_month: str | None
    usd_rate: Decimal | None  # bugünkü USD/TRY
    unpriced: list[str] = field(default_factory=list)

    def by_asset(self) -> list[AssetReturn]:
        groups: dict[int, list[LotRow]] = defaultdict(list)
        for lot in self.lots:
            groups[lot.asset.id or -1].append(lot)
        out: list[AssetReturn] = []
        for rows in groups.values():
            out.append(
                AssetReturn(
                    rows[0].asset,
                    sum((r.cost for r in rows), ZERO),
                    _sum_or_none([r.cost_real for r in rows]),
                    _sum_or_none([r.cost_usd for r in rows]),
                    _sum_or_none([r.value for r in rows]),
                    _sum_or_none([r.value_usd for r in rows]),
                )
            )
        return sorted(out, key=lambda r: -(r.value or ZERO))

    def total(self) -> AssetReturn | None:
        if not self.lots:
            return None
        return AssetReturn(
            self.lots[0].asset,
            sum((r.cost for r in self.lots), ZERO),
            _sum_or_none([r.cost_real for r in self.lots]),
            _sum_or_none([r.cost_usd for r in self.lots]),
            _sum_or_none([r.value for r in self.lots]),
            _sum_or_none([r.value_usd for r in self.lots]),
        )


@dataclass(frozen=True, slots=True)
class ContributionRow:
    """Bir aydaki para girişi/çıkışı ve o ana kadarki birikim (TL)."""

    month: str
    invested: Decimal  # yeni para (alış + bedelli)
    withdrawn: Decimal  # satıştan çekilen
    cumulative: Decimal  # o aya kadar net yatırılan
    cumulative_real: Decimal | None  # net yatırılanın TÜFE ile bugüne taşınmış hali

    @property
    def net(self) -> Decimal:
        return self.invested - self.withdrawn


@dataclass(frozen=True, slots=True)
class Benchmark:
    name: str
    nominal_pct: Decimal  # yıllık %, ör. 45.0

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "nominal_pct": str(self.nominal_pct)}


@dataclass(frozen=True, slots=True)
class BenchmarkRow:
    name: str
    nominal_pct: Decimal | None
    real_pct: Decimal | None
    is_portfolio: bool = False


@dataclass(slots=True)
class AnnualReturn:
    """Para ağırlıklı yıllık getiri (XIRR) ve son 12 ay TÜFE'si (yüzde)."""

    nominal_pct: Decimal | None
    cpi_12m_pct: Decimal | None
    since: date | None

    @property
    def real_pct(self) -> Decimal | None:
        if self.nominal_pct is None or self.cpi_12m_pct is None:
            return None
        return real_rate(self.nominal_pct / HUNDRED, self.cpi_12m_pct / HUNDRED) * HUNDRED


@dataclass(frozen=True, slots=True)
class ScenarioRow:
    purpose: Purpose
    before: Decimal
    after: Decimal

    @property
    def change(self) -> Decimal:
        return self.after - self.before

    @property
    def change_pct(self) -> Decimal | None:
        return pct(self.change, self.before) if self.before else None


@dataclass(frozen=True, slots=True)
class Preset:
    """Hazır senaryo: varlık sınıfı şokları ve kur şoku (oran)."""

    key: str
    title: str
    description: str
    class_shocks: dict[AssetClass, Decimal]
    fx_shock: Decimal = ZERO


PRESETS: tuple[Preset, ...] = (
    Preset(
        "devaluation",
        "Devalüasyon (dolar +%30)",
        "TL hızlı değer kaybeder; döviz cinsinden varlıklar ve dolara bağlı altın değer kazanır.",
        {AssetClass.PRECIOUS_METAL: Decimal("0.30")},
        Decimal("0.30"),
    ),
    Preset(
        "gold_correction",
        "Altında düzeltme (ons −%15)",
        "Ons altın ve gümüş fiyatı düşer; kur sabit.",
        {AssetClass.PRECIOUS_METAL: Decimal("-0.15")},
    ),
    Preset(
        "bist_crash",
        "Borsa sert düşüş (BIST −%25)",
        "Yerel hisseler ve büyük oranda hisse taşıyan fonlar düşer.",
        {AssetClass.BIST_STOCK: Decimal("-0.25"), AssetClass.FUND: Decimal("-0.15")},
    ),
    Preset(
        "us_crash",
        "ABD borsası düşüş (−%20)",
        "ABD hisse ve ETF'leri dolar bazında düşer; kur sabit.",
        {AssetClass.US_STOCK: Decimal("-0.20")},
    ),
    Preset(
        "bad_day",
        "Kötü gün (hepsi birden)",
        "Borsa düşüşleri ve altında düzeltme aynı anda; kur sabit.",
        {
            AssetClass.BIST_STOCK: Decimal("-0.25"),
            AssetClass.FUND: Decimal("-0.15"),
            AssetClass.US_STOCK: Decimal("-0.20"),
            AssetClass.PRECIOUS_METAL: Decimal("-0.15"),
        },
    ),
)


@dataclass(frozen=True, slots=True)
class CheckItem:
    level: str  # "ok" | "warning" | "error" | "info"
    text: str


@dataclass(slots=True)
class PurchaseCheck:
    asset: Asset
    amount: Decimal
    weight_before: Decimal
    weight_after: Decimal
    purpose_before: Decimal
    purpose_after: Decimal
    items: list[CheckItem]

    @property
    def verdict(self) -> str:
        levels = {i.level for i in self.items}
        if "error" in levels:
            return "Kurallara aykırı"
        if "warning" in levels:
            return "Dikkatli ol"
        return "Uygun"


def _sum_or_none(values: list[Decimal | None]) -> Decimal | None:
    if not values or any(v is None for v in values):
        return None
    return sum((v for v in values if v is not None), ZERO)


# --------------------------------------------------------------------------- servis
class AnalyticsService:
    def __init__(
        self,
        portfolio: PortfolioService,
        transactions: TransactionRepository,
        cpi: CpiRepository,
        market: MarketDataService,
        settings: SettingsRepository,
        insights: InsightsService,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.portfolio = portfolio
        self.transactions = transactions
        self.cpi = cpi
        self.market = market
        self.settings = settings
        self.insights = insights
        self.today = today

    # ------------------------------------------------------------------ kur yardımcıları
    def _fx_tables(self, currencies: set[Currency], start: date) -> tuple[dict, dict[Currency, Decimal]]:
        needed = (currencies | {Currency.USD}) - {Currency.TRY}
        hist = self.market.fx_history(needed, Currency.TRY, start - timedelta(days=7))
        current = self.market.fx_rates(needed, Currency.TRY)
        return hist, current

    @staticmethod
    def _rate(
        currency: Currency, day: date, hist: dict, current: dict[Currency, Decimal]
    ) -> tuple[Decimal | None, bool]:
        """`currency`nin `day` günündeki TL karşılığı; geçmiş yoksa güncel kur (yaklaşık)."""
        if currency is Currency.TRY:
            return ONE, False
        rate = value_on(hist.get(currency, {}), day)
        if rate is not None:
            return rate, False
        return current.get(currency), True

    # ------------------------------------------------------------------ alım alım getiri
    def lots(self, *, force_refresh: bool = False) -> LotReport:
        """Elde kalan her alım için maliyet (TL, reel, USD) ve bugünkü değer."""
        summary = self.portfolio.summary(Currency.TRY, force_refresh=force_refresh)
        grouped = self.transactions.by_asset()
        index = self.cpi.all()
        all_txs = [t for txs in grouped.values() for t in txs]
        start = min((t.trade_date for t in all_txs), default=self.today())
        hist, current = self._fx_tables({pv.asset.currency for pv in summary.open_positions}, start)
        usd_now = current.get(Currency.USD)

        rows: list[LotRow] = []
        unpriced: list[str] = []
        for pv in summary.open_positions:
            txs = grouped.get(pv.asset.id or -1, [])
            price_try = (
                pv.quote.price * pv.fx if pv.quote is not None and pv.fx is not None else None
            )  # summary TL raporlamada; fx = varlık PB → TL
            if price_try is None:
                unpriced.append(pv.asset.symbol)
            for lot in open_lots(txs):
                t = lot.trade
                rate, approx = self._rate(pv.asset.currency, t.trade_date, hist, current)
                if rate is None:
                    continue
                cost = lot.cost * rate
                usd_then, approx_usd = self._rate(Currency.USD, t.trade_date, hist, current)
                factor = inflation_factor(index, t.trade_date) if index else None
                value = lot.quantity * price_try if price_try is not None else None
                rows.append(
                    LotRow(
                        asset=pv.asset,
                        trade_date=t.trade_date,
                        tx_type=t.tx_type,
                        quantity=lot.quantity,
                        cost=cost,
                        cost_real=cost * factor if factor is not None else None,
                        cost_usd=cost / usd_then if usd_then else None,
                        value=value,
                        value_usd=value / usd_now if value is not None and usd_now else None,
                        approx_fx=approx or approx_usd,
                    )
                )
        rows.sort(key=lambda r: (r.trade_date, r.asset.symbol))
        return LotReport(rows, latest_month(index), usd_now, unpriced)

    # ------------------------------------------------------------------ para akışı
    def contributions(self) -> list[ContributionRow]:
        """Ay ay yatırılan ve çekilen para, birikimli net tutar ve TÜFE'li karşılığı (TL)."""
        assets = {a.id: a for a in self.portfolio.assets.list()}
        txs = [t for t in self.transactions.list() if t.tx_type.is_new_money or t.tx_type is TxType.SELL]
        if not txs:
            return []
        start = min(t.trade_date for t in txs)
        hist, current = self._fx_tables({a.currency for a in assets.values()}, start)
        index = self.cpi.all()
        invested: dict[str, Decimal] = defaultdict(lambda: ZERO)
        withdrawn: dict[str, Decimal] = defaultdict(lambda: ZERO)
        real_by_month: dict[str, Decimal | None] = {}
        for t in txs:
            asset = assets.get(t.asset_id)
            if asset is None:
                continue
            rate, _ = self._rate(asset.currency, t.trade_date, hist, current)
            if rate is None:
                continue
            key = month_key(t.trade_date)
            if t.tx_type.is_new_money:
                amount = (t.gross_amount + t.fee) * rate
                invested[key] += amount
            else:
                amount = -(t.gross_amount - t.fee) * rate
                withdrawn[key] += -amount
            factor = inflation_factor(index, t.trade_date) if index else None
            prev = real_by_month.get(key, ZERO)
            real_by_month[key] = None if factor is None or prev is None else prev + amount * factor

        rows: list[ContributionRow] = []
        month = month_start(start)
        last = month_start(self.today())
        cumulative = ZERO
        cumulative_real: Decimal | None = ZERO
        while month <= last:
            key = month_key(month)
            cumulative += invested.get(key, ZERO) - withdrawn.get(key, ZERO)
            if key in real_by_month:
                part = real_by_month[key]
                cumulative_real = None if part is None or cumulative_real is None else cumulative_real + part
            rows.append(
                ContributionRow(key, invested.get(key, ZERO), withdrawn.get(key, ZERO), cumulative, cumulative_real)
            )
            month = add_months(month, 1)
        return rows

    # ------------------------------------------------------------------ yıllık getiri
    def cpi_12m(self) -> Decimal | None:
        """Eldeki son TÜFE ayına göre yıllık enflasyon (yüzde)."""
        index = self.cpi.all()
        last = latest_month(index)
        if last is None:
            return None
        y, m = (int(x) for x in last.split("-"))
        prev = cpi_at(index, f"{y - 1:04d}-{m:02d}")
        if prev is None or prev <= 0 or f"{y - 1:04d}-{m:02d}" < min(index):
            return None
        return (index[last] / prev - ONE) * HUNDRED

    def annual_return(self) -> AnnualReturn:
        """Portföyün para ağırlıklı yıllık getirisi (XIRR, TL).

        Alış/bedelli eksi, satış artı akış; bugünkü toplam değer son günde artı akış olarak eklenir.
        Fiyatı olmayan açık pozisyon varsa hesap yapılmaz (değer eksik kalırdı).
        """
        summary = self.portfolio.summary(Currency.TRY)
        cpi = self.cpi_12m()
        if summary.unpriced or not summary.open_positions:
            return AnnualReturn(None, cpi, None)
        assets = {a.id: a for a in self.portfolio.assets.list()}
        txs = [t for t in self.transactions.list() if t.tx_type.is_new_money or t.tx_type is TxType.SELL]
        if not txs:
            return AnnualReturn(None, cpi, None)
        start = min(t.trade_date for t in txs)
        hist, current = self._fx_tables({a.currency for a in assets.values()}, start)
        flows: list[tuple[date, Decimal]] = []
        for t in txs:
            asset = assets.get(t.asset_id)
            if asset is None:
                continue
            rate, _ = self._rate(asset.currency, t.trade_date, hist, current)
            if rate is None:
                return AnnualReturn(None, cpi, start)
            if t.tx_type.is_new_money:
                flows.append((t.trade_date, -(t.gross_amount + t.fee) * rate))
            else:
                flows.append((t.trade_date, (t.gross_amount - t.fee) * rate))
        flows.append((self.today(), summary.total_value))
        if (self.today() - start).days < 30:
            return AnnualReturn(None, cpi, start)  # bir aydan kısa sürede yıllıklandırma yanıltır
        rate = xirr(flows)
        return AnnualReturn(None if rate is None else rate * HUNDRED, cpi, start)

    # ------------------------------------------------------------------ kıyas
    def benchmarks(self) -> list[Benchmark]:
        raw = self.settings.get(BENCHMARKS_KEY)
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return [Benchmark(str(d["name"]), Decimal(str(d["nominal_pct"]))) for d in data]
        except (ValueError, KeyError, TypeError):
            return []

    def save_benchmarks(self, items: list[Benchmark]) -> None:
        names = [b.name.strip() for b in items]
        if any(not n for n in names):
            raise ValidationError("Kıyas aracının adı boş olamaz")
        if len(set(n.casefold() for n in names)) != len(names):
            raise ValidationError("Aynı adla iki kıyas aracı olamaz")
        if any(b.nominal_pct <= -100 for b in items):
            raise ValidationError("Yıllık getiri -%100'den büyük olmalı")
        self.settings.set(BENCHMARKS_KEY, json.dumps([b.to_dict() for b in items], ensure_ascii=False))

    def benchmark_table(self, annual: AnnualReturn | None = None) -> list[BenchmarkRow]:
        """Kullanıcının girdiği araçların yıllık nominal ve reel getirisi; en üstte portföyün kendisi."""
        annual = annual or self.annual_return()
        cpi = annual.cpi_12m_pct
        rows = [BenchmarkRow("Portföyün (yıllık, para ağırlıklı)", annual.nominal_pct, annual.real_pct, True)]
        for b in self.benchmarks():
            real = real_rate(b.nominal_pct / HUNDRED, cpi / HUNDRED) * HUNDRED if cpi is not None else None
            rows.append(BenchmarkRow(b.name, b.nominal_pct, real))
        return rows

    # ------------------------------------------------------------------ senaryo
    @staticmethod
    def scenario_by_purpose(
        summary: PortfolioSummary, class_shocks: dict[AssetClass, Decimal], fx_shock: Decimal
    ) -> list[ScenarioRow]:
        """Şokların her amaca etkisi (raporlama para biriminde)."""
        shocks = {k.value: v for k, v in class_shocks.items()}
        before: dict[Purpose, Decimal] = defaultdict(lambda: ZERO)
        after: dict[Purpose, Decimal] = defaultdict(lambda: ZERO)
        for pv in summary.open_positions:
            v = pv.market_value_base
            if v is None:
                continue
            foreign = pv.asset.currency is not summary.base_currency
            before[pv.asset.purpose] += v
            after[pv.asset.purpose] += apply_shocks([(v, pv.asset.asset_class.value, foreign)], shocks, fx_shock)
        order = [*Purpose.targeted(), Purpose.UNASSIGNED]
        return [ScenarioRow(p, before[p], after[p]) for p in order if before.get(p)]

    # ------------------------------------------------------------------ alım öncesi kontrol
    def purchase_check(
        self, summary: PortfolioSummary, asset: Asset, amount: Decimal, rules: PlanRules | None = None
    ) -> PurchaseCheck:
        """Bir varlığa `amount` (raporlama para biriminde) eklenirse kurallara ne olur?"""
        if amount <= 0:
            raise ValidationError("Tutar sıfırdan büyük olmalı")
        rules = rules or self.insights.rules()
        total = summary.total_value
        current = next((pv.market_value_base or ZERO for pv in summary.open_positions if pv.asset.id == asset.id), ZERO)
        alloc = {r.purpose: r for r in self.insights.purpose_allocation(summary, rules)}
        purpose_value = alloc[asset.purpose].value if asset.purpose in alloc else ZERO
        new_total = total + amount
        w_before = current / total if total > 0 else ZERO
        w_after = (current + amount) / new_total
        p_before = purpose_value / total if total > 0 else ZERO
        p_after = (purpose_value + amount) / new_total
        target = rules.targets.get(asset.purpose)
        items: list[CheckItem] = []

        limit = rules.single_position_max
        if w_after > limit:
            items.append(
                CheckItem(
                    "error",
                    f"Alımdan sonra {asset.symbol} portföyün %{w_after * HUNDRED:.1f}'i olur; "
                    f"tek kalem sınırı %{limit * HUNDRED:.0f}.",
                )
            )
        else:
            items.append(
                CheckItem("ok", f"Tek kalem sınırı içinde: %{w_after * HUNDRED:.1f} (sınır %{limit * HUNDRED:.0f}).")
            )

        if asset.purpose is Purpose.UNASSIGNED:
            items.append(CheckItem("warning", "Varlığın amacı atanmamış; önce bir amaç seç."))
        elif target is not None:
            tol = rules.drift_tolerance
            gap_after = (p_after - target) * HUNDRED
            if p_after > target + tol:
                level = "error" if p_before > target + tol else "warning"
                items.append(
                    CheckItem(
                        level,
                        f"{asset.purpose.label} payı %{p_after * HUNDRED:.1f} olur; hedef %{target * HUNDRED:.0f} "
                        f"(+{gap_after:.1f} puan). Para hedefin altındaki amaçlara daha çok yarar.",
                    )
                )
            elif p_before < target:
                items.append(
                    CheckItem(
                        "ok",
                        f"{asset.purpose.label} hedefin altındaydı (%{p_before * HUNDRED:.1f} → "
                        f"%{p_after * HUNDRED:.1f}, hedef %{target * HUNDRED:.0f}); alım dağılımı düzeltir.",
                    )
                )
            else:
                items.append(
                    CheckItem(
                        "ok",
                        f"{asset.purpose.label} payı tolerans içinde kalır: %{p_after * HUNDRED:.1f} "
                        f"(hedef %{target * HUNDRED:.0f}).",
                    )
                )

        if asset.purpose is Purpose.EXPERIMENT and p_after > rules.experiment_budget:
            items.append(
                CheckItem(
                    "warning",
                    f"Deneme bütçesi %{rules.experiment_budget * HUNDRED:.0f}; alımdan sonra deneme payı "
                    f"%{p_after * HUNDRED:.1f}.",
                )
            )
        quote = next((pv.quote for pv in summary.open_positions if pv.asset.id == asset.id), None)
        if quote is not None and quote.stale:
            items.append(CheckItem("info", "Bu varlığın fiyatı güncellenemedi; hesap son bilinen fiyatla yapıldı."))
        if summary.unpriced:
            items.append(
                CheckItem(
                    "info",
                    "Fiyatı olmayan varlıklar paylara katılmadı: " + ", ".join(a.symbol for a in summary.unpriced),
                )
            )
        return PurchaseCheck(asset, amount, w_before, w_after, p_before, p_after, items)


def price_freshness(quote, today: date | None = None, *, max_age_days: int = 3) -> str:  # type: ignore[no-untyped-def]
    """Fiyatın ne kadar güvenilir olduğunu tek kelimeyle söyler (tablolar için)."""
    if quote is None:
        return "Fiyat yok"
    if quote.source in ("manual", "cash"):
        return "Manuel"
    if quote.stale:
        if quote.as_of is not None and ((today or date.today()) - quote.as_of.date()).days > max_age_days:
            return f"Eski ({quote.as_of:%d.%m})"
        return "Önbellek"
    return "Güncel"


__all__ = [
    "PRESETS",
    "AnalyticsService",
    "AnnualReturn",
    "AssetReturn",
    "Benchmark",
    "BenchmarkRow",
    "CheckItem",
    "ContributionRow",
    "LotReport",
    "LotRow",
    "Preset",
    "PurchaseCheck",
    "ScenarioRow",
    "price_freshness",
]
