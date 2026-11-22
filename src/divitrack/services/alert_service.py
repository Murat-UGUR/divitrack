"""Alarmlar: fiyat eşiği, portföy payı ve zirveden düşüş; tetiklenince isteğe bağlı Telegram bildirimi."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from divitrack.core.entities import Alert
from divitrack.core.enums import AlertKind, Currency
from divitrack.core.exceptions import ValidationError
from divitrack.core.risk import drawdown, performance_index
from divitrack.repositories.sqlite import AlertRepository, SnapshotRepository
from divitrack.services.notify import TelegramNotifier
from divitrack.services.portfolio_service import PortfolioService, PortfolioSummary


@dataclass(frozen=True, slots=True)
class AlertState:
    alert: Alert
    label: str  # "XAU-GRAM fiyatı 5.500 TL altına inerse"
    current: Decimal | None
    triggered: bool

    @property
    def message(self) -> str:
        cur = (
            "—"
            if self.current is None
            else f"{self.current:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )
        unit = "%" if self.alert.kind.unit == "%" else ""
        note = f" ({self.alert.note})" if self.alert.note else ""
        return f"🔔 {self.label}{note} — şu an {cur}{unit}"


def _fmt(v: Decimal) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").removesuffix(",00")


class AlertService:
    def __init__(
        self,
        alerts: AlertRepository,
        portfolio: PortfolioService,
        snapshots: SnapshotRepository,
        notifier: TelegramNotifier | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.alerts = alerts
        self.portfolio = portfolio
        self.snapshots = snapshots
        self.notifier = notifier
        self.today = today

    def add(self, alert: Alert) -> Alert:
        if alert.asset_id is not None:
            self.portfolio.assets.get(alert.asset_id)
        return self.alerts.add(alert)

    def delete(self, alert_id: int) -> None:
        self.alerts.delete(alert_id)

    def set_active(self, alert: Alert, active: bool) -> None:
        alert.active = active
        self.alerts.update(alert)

    def evaluate(self, summary: PortfolioSummary | None = None) -> list[AlertState]:
        summary = summary or self.portfolio.summary(Currency.TRY)
        views = {pv.asset.id: pv for pv in summary.open_positions}
        names = {a.id: a.symbol for a in self.portfolio.assets.list()}
        dd = None
        out: list[AlertState] = []
        for al in self.alerts.list():
            sym = names.get(al.asset_id, "?") if al.asset_id is not None else ""
            current: Decimal | None = None
            if al.kind in (AlertKind.PRICE_BELOW, AlertKind.PRICE_ABOVE):
                pv = views.get(al.asset_id)
                if pv is not None and pv.quote is not None:
                    current = pv.quote.price
                elif al.asset_id is not None:
                    q = self.portfolio.market.quotes_for([self.portfolio.assets.get(al.asset_id)]).get(al.asset_id)
                    current = q.price if q else None
                below = al.kind is AlertKind.PRICE_BELOW
                hit = current is not None and (current <= al.threshold if below else current >= al.threshold)
                label = f"{sym} fiyatı {_fmt(al.threshold)} {'altına inerse' if below else 'üstüne çıkarsa'}"
            elif al.kind is AlertKind.WEIGHT_ABOVE:
                pv = views.get(al.asset_id)
                if pv is not None and pv.market_value_base is not None and summary.total_value > 0:
                    current = pv.market_value_base / summary.total_value * 100
                hit = current is not None and current > al.threshold
                label = f"{sym} portföyün %{_fmt(al.threshold)}'ini aşarsa"
            else:
                if dd is None:
                    snaps = self.snapshots.list(summary.base_currency)
                    dd = drawdown(performance_index([(s.snapshot_date, s.total_value, s.total_cost) for s in snaps]))
                current = -dd.current_pct if dd is not None else None
                hit = current is not None and current >= al.threshold
                label = f"Portföy zirveden %{_fmt(al.threshold)} düşerse"
            out.append(AlertState(al, label, current, bool(al.active and hit)))
        return out

    def check(self, summary: PortfolioSummary | None = None, *, notify: bool = True) -> list[AlertState]:
        """Tetiklenen alarmları döndürür; her alarm için günde en fazla bir bildirim gönderilir."""
        today = self.today()
        fired = [s for s in self.evaluate(summary) if s.triggered]
        for s in fired:
            if s.alert.last_triggered == today:
                continue
            if notify and self.notifier is not None and self.notifier.configured:
                self.notifier.send(s.message)
            s.alert.last_triggered = today
            self.alerts.update(s.alert)
        return fired


def validate_threshold(kind: AlertKind, value: Decimal) -> None:
    if value <= 0:
        raise ValidationError("Eşik sıfırdan büyük olmalı")
    if kind.unit == "%" and value > 100:
        raise ValidationError("Yüzde eşiği 100'den büyük olamaz")
