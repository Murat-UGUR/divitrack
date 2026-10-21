"""Servis çıktılarını UI tablolarına/grafik verisine dönüştürür (pandas; Streamlit bağımsız)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pandas as pd

from divitrack.core.entities import Asset, CashFlow, Category, Dividend, Transaction
from divitrack.core.enums import AssetClass, Currency
from divitrack.core.inflation import monthly_changes
from divitrack.services.budget_service import MonthSummary
from divitrack.services.dividend_engine import DividendEvent, EventKind, YieldInfo
from divitrack.services.insights_service import AssetCard, PlanRow, PurposeRow, RealReturnReport
from divitrack.services.portfolio_service import PortfolioSummary

# Kategoriler için sabit sıra → sabit renk (renk varlığa bağlıdır, sıralamaya değil)
CLASS_ORDER = [
    AssetClass.BIST_STOCK,
    AssetClass.US_STOCK,
    AssetClass.CASH,
    AssetClass.PRECIOUS_METAL,
    AssetClass.FUND,
]
EVENT_ORDER = [EventKind.PAID, EventKind.EXPECTED, EventKind.PROJECTED]


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def positions_frame(summary: PortfolioSummary, *, include_closed: bool = False) -> pd.DataFrame:
    total = summary.total_value
    rows = []
    for p in summary.positions:
        if not include_closed and not p.state.is_open:
            continue
        mv = p.market_value_base
        rows.append(
            {
                "Sembol": p.asset.symbol,
                "Ad": p.asset.name,
                "Sınıf": p.asset.asset_class.label,
                "Amaç": p.asset.purpose.label,
                "PB": p.asset.currency.value,
                "Adet": _f(p.state.quantity),
                "Ort. Maliyet": _f(p.state.avg_cost),
                "Fiyat": _f(p.quote.price) if p.quote else None,
                "Günlük %": _f(p.quote.change_pct) if p.quote else None,
                f"Değer ({summary.base_currency.value})": _f(mv),
                "K/Z": _f(p.unrealized_pnl_base),
                "K/Z %": _f(p.unrealized_pct),
                "Ağırlık %": _f(mv / total * 100) if mv is not None and total else None,
                "Gerçekleşen K/Z": _f(p.realized_pnl_base),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(f"Değer ({summary.base_currency.value})", ascending=False, na_position="last")
    return df


def allocation_frame(summary: PortfolioSummary, *, by: str = "class") -> pd.DataFrame:
    if by == "class":
        rows = [
            {"Etiket": c.label, "Değer": float(summary.allocation_by_class[c])}
            for c in CLASS_ORDER
            if summary.allocation_by_class.get(c)
        ]
    else:
        rows = [
            {"Etiket": p.asset.symbol, "Değer": float(p.market_value_base)}
            for p in summary.open_positions
            if p.market_value_base
        ]
        rows.sort(key=lambda r: r["Değer"], reverse=True)
        if len(rows) > 7:  # 8+ dilim okunmaz → "Diğer"
            other = sum(r["Değer"] for r in rows[7:])
            rows = [*rows[:7], {"Etiket": "Diğer", "Değer": other}]
    return pd.DataFrame(rows, columns=["Etiket", "Değer"])


def events_frame(events: Sequence[DividendEvent], rates: dict[Currency, Decimal], base: Currency) -> pd.DataFrame:
    rows = []
    for e in events:
        rate = rates.get(e.currency)
        rows.append(
            {
                "Tarih": e.pay_date,
                "Ay": e.pay_date.strftime("%Y-%m"),
                "Sembol": e.symbol,
                "Durum": e.kind.label,
                "Hisse Başı": float(e.per_share),
                "Adet": float(e.quantity),
                "PB": e.currency.value,
                "Brüt": float(e.gross),
                "Net": float(e.net),
                f"Net ({base.value})": float(e.net * rate) if rate is not None else None,
                "_kind": e.kind.value,
                "_id": e.dividend_id,
            }
        )
    return pd.DataFrame(rows)


def monthly_income(events_df: pd.DataFrame, base: Currency, start: date, months: int = 24) -> pd.DataFrame:
    """Ay × durum tablosu; boş aylar da 0 ile yer alır (takvim kesintisiz görünür)."""
    col = f"Net ({base.value})"
    periods = pd.period_range(pd.Timestamp(start), periods=months, freq="M").strftime("%Y-%m")
    frame = pd.DataFrame({"Ay": list(periods)})
    for kind in EVENT_ORDER:
        if events_df.empty:
            frame[kind.label] = 0.0
            continue
        sub = events_df[events_df["_kind"] == kind.value].groupby("Ay")[col].sum()
        frame[kind.label] = frame["Ay"].map(sub).fillna(0.0).astype(float)
    return frame


def yields_frame(infos: Sequence[YieldInfo], assets: dict[int, Asset]) -> pd.DataFrame:
    rows = [
        {
            "Sembol": i.symbol,
            "PB": assets[i.asset_id].currency.value,
            "Sıklık": i.frequency_label,
            "TTM Temettü/Hisse": float(i.ttm_per_share),
            "Temettü Verimi %": _f(i.current_yield),
            "Maliyete Göre Verim %": _f(i.yield_on_cost),
            "Yıllık Brüt": float(i.annual_income_gross),
            "Yıllık Net": float(i.annual_income_net),
        }
        for i in infos
    ]
    return pd.DataFrame(rows)


def transactions_frame(txs: Sequence[Transaction], assets: dict[int, Asset]) -> pd.DataFrame:
    rows = [
        {
            "ID": t.id,
            "Tarih": t.trade_date,
            "Sembol": assets[t.asset_id].symbol,
            "İşlem": t.tx_type.label,
            "Adet": float(t.quantity),
            "Fiyat": float(t.price),
            "Komisyon": float(t.fee),
            "Tutar": float(t.gross_amount),
            "PB": assets[t.asset_id].currency.value,
            "Not": t.note,
        }
        for t in sorted(txs, key=lambda t: (t.trade_date, t.id or 0), reverse=True)
    ]
    return pd.DataFrame(rows)


def dividends_frame(divs: Sequence[Dividend], assets: dict[int, Asset]) -> pd.DataFrame:
    rows = [
        {
            "ID": d.id,
            "Ödeme": d.pay_date,
            "Hak Kullanım": d.ex_date,
            "Sembol": assets[d.asset_id].symbol if d.asset_id in assets else "?",
            "Durum": d.status.label,
            "Hisse Başı": float(d.amount_per_share),
            "Adet": float(d.quantity),
            "Brüt": float(d.gross),
            "Stopaj %": float(d.withholding_rate * 100),
            "Net": float(d.net),
            "PB": d.currency.value,
            "Not": d.note,
        }
        for d in divs
    ]
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- analiz
def purpose_frame(rows: Sequence[PurposeRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Amaç": r.purpose.label,
                "Değer": float(r.value),
                "Pay %": float(r.weight * 100),
                "Hedef %": float(r.target * 100),
                "Sapma (puan)": float(r.drift_pp),
                "Varlıklar": ", ".join(r.symbols),
            }
            for r in rows
        ],
        columns=["Amaç", "Değer", "Pay %", "Hedef %", "Sapma (puan)", "Varlıklar"],
    )


def real_returns_frame(report: RealReturnReport) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Sembol": r.asset.symbol + ("" if r.is_open else " (kapalı)"),
                "Amaç": r.asset.purpose.label,
                "Yatırılan (TL)": float(r.invested),
                "Bugünkü karşılığı (TL)": _f(r.invested_real),
                "Güncel değer (TL)": float(r.value),
                "Nominal %": _f(r.nominal_pct),
                "Reel %": _f(r.real_pct),
                "Reel K/Z (TL)": _f(r.real_gain),
                "Kur": "≈ güncel" if r.approx_fx else "",
            }
            for r in report.rows
        ],
        columns=[
            "Sembol",
            "Amaç",
            "Yatırılan (TL)",
            "Bugünkü karşılığı (TL)",
            "Güncel değer (TL)",
            "Nominal %",
            "Reel %",
            "Reel K/Z (TL)",
            "Kur",
        ],
    )


def plan_frame(rows: Sequence[PlanRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Amaç": r.purpose.label,
                "Yatırılacak": float(r.amount),
                "Şu anki pay %": float(r.weight_before * 100),
                "Sonraki pay %": float(r.weight_after * 100),
                "Hedef %": float(r.target * 100),
                "Mevcut varlıklar": ", ".join(r.symbols) or "—",
            }
            for r in rows
        ],
        columns=["Amaç", "Yatırılacak", "Şu anki pay %", "Sonraki pay %", "Hedef %", "Mevcut varlıklar"],
    )


# --------------------------------------------------------------------------- bütçe
def budget_lines_frame(summary: MonthSummary) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Kategori": ln.category.name,
                "Tür": ln.category.kind.label,
                "Gerçekleşen": float(ln.actual),
                "Bütçe": _f(ln.budget),
                "Kalan": _f(ln.remaining),
                "Kullanım %": _f(ln.used_pct),
            }
            for ln in summary.lines
        ],
        columns=["Kategori", "Tür", "Gerçekleşen", "Bütçe", "Kalan", "Kullanım %"],
    )


def budget_history_frame(history: Sequence[MonthSummary]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Ay": m.label,
                "Gelir": float(m.income),
                "Gider": float(m.expense),
                "Net": float(m.net),
                "Tasarruf %": _f(m.savings_rate),
            }
            for m in history
        ],
        columns=["Ay", "Gelir", "Gider", "Net", "Tasarruf %"],
    )


def flows_frame(flows: Sequence[CashFlow], categories: dict[int, Category]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ID": f.id,
                "Tarih": f.flow_date,
                "Kategori": categories[f.category_id].name if f.category_id in categories else "?",
                "Tür": categories[f.category_id].kind.label if f.category_id in categories else "",
                "Tutar": float(f.amount),
                "Not": f.note,
            }
            for f in flows
        ],
        columns=["ID", "Tarih", "Kategori", "Tür", "Tutar", "Not"],
    )


def cpi_frame(index: dict[str, Decimal]) -> pd.DataFrame:
    changes = dict(monthly_changes(index))
    return pd.DataFrame(
        [{"Ay": m, "Endeks": float(v), "Aylık %": _f(changes.get(m))} for m, v in sorted(index.items(), reverse=True)],
        columns=["Ay", "Endeks", "Aylık %"],
    )


def report_card_frame(cards: Sequence[AssetCard]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Durum": c.grade,
                "Sembol": c.asset.symbol,
                "Amaç": c.asset.purpose.label,
                "Ağırlık %": _f(c.weight * 100) if c.weight is not None else None,
                "K/Z %": _f(c.nominal_pct),
                "Reel %": _f(c.real_pct),
                "Temettü verimi %": _f(c.dividend_yield),
                "İşaretler": " · ".join(c.flags) or "—",
            }
            for c in cards
        ],
        columns=["Durum", "Sembol", "Amaç", "Ağırlık %", "K/Z %", "Reel %", "Temettü verimi %", "İşaretler"],
    )


def score_history_frame(history: Sequence[tuple[str, Decimal]]) -> pd.DataFrame:
    return pd.DataFrame([{"Ay": m, "Puan": float(s)} for m, s in history], columns=["Ay", "Puan"])
