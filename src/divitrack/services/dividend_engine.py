"""Temettü motoru: verim oranları, ödeme takvimi ve 12 aylık gelir projeksiyonu."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from divitrack.core.calculations import (
    compute_position,
    frequency_label,
    payment_frequency,
    project_payments,
    ttm_dividend_per_share,
)
from divitrack.core.entities import Asset, Dividend, PositionState, PriceQuote, Transaction
from divitrack.core.enums import AssetClass, Currency, DividendStatus
from divitrack.core.money import ONE, ZERO, pct
from divitrack.core.ports import PriceProvider
from divitrack.repositories.sqlite import DividendRepository, SettingsRepository
from divitrack.services.market.symbols import provider_symbol

log = logging.getLogger(__name__)

# Varsayılan stopaj oranları — mevzuat değişebilir, Ayarlar sayfasından güncellenebilir.
DEFAULT_WITHHOLDING: dict[AssetClass, Decimal] = {
    AssetClass.BIST_STOCK: Decimal("0.15"),
    AssetClass.US_STOCK: Decimal("0.20"),
    AssetClass.PRECIOUS_METAL: ZERO,
    AssetClass.CASH: ZERO,
}
WITHHOLDING_KEY = "withholding.{}"


class EventKind(StrEnum):
    PAID = "PAID"
    EXPECTED = "EXPECTED"
    PROJECTED = "PROJECTED"

    @property
    def label(self) -> str:
        return {"PAID": "Ödendi", "EXPECTED": "Beklenen", "PROJECTED": "Tahmini"}[self.value]


@dataclass(frozen=True, slots=True)
class DividendEvent:
    asset_id: int
    symbol: str
    pay_date: date
    per_share: Decimal
    quantity: Decimal
    gross: Decimal
    net: Decimal
    currency: Currency
    kind: EventKind
    dividend_id: int | None = None


@dataclass(frozen=True, slots=True)
class YieldInfo:
    asset_id: int
    symbol: str
    ttm_per_share: Decimal
    frequency: int
    current_yield: Decimal | None  # TTM temettü / güncel fiyat (%)
    yield_on_cost: Decimal | None  # TTM temettü / ortalama maliyet (%)
    annual_income_gross: Decimal  # TTM hisse başı × adet (varlık para biriminde)
    annual_income_net: Decimal

    @property
    def frequency_label(self) -> str:
        return frequency_label(self.frequency)


class DividendEngine:
    def __init__(
        self,
        dividends: DividendRepository,
        settings: SettingsRepository,
        provider: PriceProvider | None,
        *,
        history_ttl: timedelta = timedelta(hours=12),
        today: Callable[[], date] = date.today,
    ) -> None:
        self.dividends = dividends
        self.settings = settings
        self.provider = provider
        self.history_ttl = history_ttl
        self.today = today
        self._history: dict[str, tuple[datetime, list[tuple[date, Decimal]]]] = {}

    # ------------------------------------------------------------------ ayarlar
    def withholding_rate(self, asset_class: AssetClass) -> Decimal:
        raw = self.settings.get(WITHHOLDING_KEY.format(asset_class.value))
        return Decimal(raw) if raw is not None else DEFAULT_WITHHOLDING[asset_class]

    def set_withholding_rate(self, asset_class: AssetClass, rate: Decimal) -> None:
        if not ZERO <= rate < ONE:
            raise ValueError("Oran 0 ile 1 arasında olmalı")
        self.settings.set(WITHHOLDING_KEY.format(asset_class.value), str(rate))

    # ------------------------------------------------------------------ kayıtlar
    def record(self, dividend: Dividend) -> Dividend:
        return self.dividends.add(dividend)

    def mark_paid(self, dividend_id: int) -> None:
        self.dividends.set_status(dividend_id, DividendStatus.PAID)

    def delete(self, dividend_id: int) -> None:
        self.dividends.delete(dividend_id)

    # ------------------------------------------------------------------ geçmiş
    def history(self, asset: Asset) -> list[tuple[date, Decimal]]:
        if asset.asset_class not in (AssetClass.BIST_STOCK, AssetClass.US_STOCK):
            return []
        sym = provider_symbol(asset)
        if sym is None or self.provider is None:
            return []
        cached = self._history.get(sym)
        if cached and datetime.now() - cached[0] < self.history_ttl:
            return cached[1]
        try:
            payments = self.provider.fetch_dividends(sym)
        except Exception as exc:
            log.warning("Temettü geçmişi alınamadı (%s): %s", sym, exc)
            return cached[1] if cached else []
        self._history[sym] = (datetime.now(), payments)
        return payments

    # ------------------------------------------------------------------ analiz
    def yield_info(self, asset: Asset, state: PositionState, quote: PriceQuote | None) -> YieldInfo:
        assert asset.id is not None
        today = self.today()
        hist = self.history(asset)
        ttm = ttm_dividend_per_share(hist, today)
        gross = ttm * state.quantity
        rate = self.withholding_rate(asset.asset_class)
        return YieldInfo(
            asset_id=asset.id,
            symbol=asset.symbol,
            ttm_per_share=ttm,
            frequency=payment_frequency(hist, today),
            current_yield=pct(ttm, quote.price) if quote and ttm else None,
            yield_on_cost=pct(ttm, state.avg_cost) if ttm else None,
            annual_income_gross=gross,
            annual_income_net=gross * (ONE - rate),
        )

    def events(
        self,
        holdings: list[tuple[Asset, PositionState]],
        *,
        horizon_days: int = 365,
        include_projections: bool = True,
    ) -> list[DividendEvent]:
        """Kayıtlı (ödenen/beklenen) temettüler + açık pozisyonlar için tahmini ödemeler.

        Bir varlık için gelecekte tarihli 'Beklenen' kayıt girilmişse o varlık için
        tahmin üretilmez — kullanıcının girdiği bilgi önceliklidir.
        """
        today = self.today()
        by_id = {a.id: a for a, _ in holdings}
        events: list[DividendEvent] = []
        user_expected: set[int] = set()
        for d in self.dividends.list():
            asset = by_id.get(d.asset_id)
            if asset is None:
                continue
            kind = EventKind.PAID if d.status is DividendStatus.PAID else EventKind.EXPECTED
            if kind is EventKind.EXPECTED and d.pay_date >= today:
                user_expected.add(d.asset_id)
            events.append(
                DividendEvent(
                    d.asset_id,
                    asset.symbol,
                    d.pay_date,
                    d.amount_per_share,
                    d.quantity,
                    d.gross,
                    d.net,
                    d.currency,
                    kind,
                    d.id,
                )
            )
        if include_projections:
            for asset, state in holdings:
                if not state.is_open or asset.id in user_expected:
                    continue
                assert asset.id is not None
                rate = self.withholding_rate(asset.asset_class)
                for pay_date, per_share in project_payments(self.history(asset), today, horizon_days):
                    gross = per_share * state.quantity
                    events.append(
                        DividendEvent(
                            asset.id,
                            asset.symbol,
                            pay_date,
                            per_share,
                            state.quantity,
                            gross,
                            gross * (ONE - rate),
                            asset.currency,
                            EventKind.PROJECTED,
                        )
                    )
        return sorted(events, key=lambda e: e.pay_date)

    def suggest_from_history(self, asset: Asset, transactions: list[Transaction]) -> list[Dividend]:
        """Sağlayıcı geçmişinden, elde tutulan döneme denk gelen temettüleri 'Ödendi' kaydı olarak önerir.

        Yahoo'daki tarih hak kullanım (ex-date) tarihidir; adet, ex-date'ten bir gün önceki
        pozisyondan hesaplanır. Ödeme tarihi yaklaşık olarak ex-date kabul edilir.
        """
        assert asset.id is not None
        today = self.today()
        rate = self.withholding_rate(asset.asset_class)
        known = {(d.asset_id, d.ex_date or d.pay_date) for d in self.dividends.list()}
        out: list[Dividend] = []
        for ex_date, amt in self.history(asset):
            if ex_date > today or (asset.id, ex_date) in known:
                continue
            qty = compute_position(transactions, as_of=ex_date - timedelta(days=1)).quantity
            if qty <= 0:
                continue
            out.append(
                Dividend(
                    asset_id=asset.id,
                    ex_date=ex_date,
                    pay_date=ex_date,
                    amount_per_share=amt,
                    quantity=qty,
                    currency=asset.currency,
                    status=DividendStatus.PAID,
                    withholding_rate=rate,
                    note="Yahoo geçmişinden içe aktarıldı",
                )
            )
        return out
