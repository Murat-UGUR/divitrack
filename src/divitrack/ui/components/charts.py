"""Plotly grafikleri. Renkler sabit sırayla atanır; her seri her zaman aynı rengi taşır."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from divitrack.core.enums import AssetClass, Purpose
from divitrack.services.dividend_engine import EventKind

# Koyu yüzey için doğrulanmış kategorik palet (mavi, turuncu, su yeşili, sarı)
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
SURFACE = "#1a1a19"
TEXT = "#ffffff"
TEXT_MUTED = "#c3c2b7"
GRID = "rgba(195,194,183,0.15)"
NEUTRAL = "#8a8980"

CLASS_COLORS = {
    AssetClass.BIST_STOCK.label: SERIES[0],
    AssetClass.US_STOCK.label: SERIES[1],
    AssetClass.CASH.label: SERIES[2],
    AssetClass.PRECIOUS_METAL.label: SERIES[3],
    AssetClass.FUND.label: SERIES[4],
}
PURPOSE_COLORS = {
    Purpose.DEFENSIVE.label: SERIES[3],
    Purpose.GROWTH.label: SERIES[0],
    Purpose.DIVIDEND.label: SERIES[2],
    Purpose.EXPERIMENT.label: SERIES[4],
    Purpose.UNASSIGNED.label: NEUTRAL,
}
EVENT_COLORS = {
    EventKind.PAID.label: SERIES[0],
    EventKind.EXPECTED.label: SERIES[1],
    EventKind.PROJECTED.label: SERIES[2],
}


def _layout(fig: go.Figure, height: int = 360) -> go.Figure:
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": TEXT_MUTED, "size": 13},
        legend={"orientation": "h", "y": -0.12, "x": 0, "font": {"color": TEXT_MUTED}},
        hoverlabel={"bgcolor": SURFACE, "font": {"color": TEXT}},
        separators=",.",
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, tickfont={"color": TEXT_MUTED})
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont={"color": TEXT_MUTED})
    return fig


def allocation_donut(df: pd.DataFrame, currency_symbol: str, *, by_class: bool) -> go.Figure:
    colors = (
        [CLASS_COLORS.get(lbl, NEUTRAL) for lbl in df["Etiket"]]
        if by_class
        else [NEUTRAL if lbl == "Diğer" else SERIES[i % len(SERIES)] for i, lbl in enumerate(df["Etiket"])]
    )
    fig = go.Figure(
        go.Pie(
            labels=df["Etiket"],
            values=df["Değer"],
            hole=0.62,
            sort=False,
            direction="clockwise",
            marker={"colors": colors, "line": {"color": SURFACE, "width": 2}},
            textinfo="percent",
            textfont={"color": TEXT},
            hovertemplate=f"<b>%{{label}}</b><br>{currency_symbol}%{{value:,.2f}}<br>%{{percent}}<extra></extra>",
        )
    )
    fig.update_layout(showlegend=True)
    return _layout(fig)


def growth_line(df: pd.DataFrame, currency_symbol: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["Tarih"],
            y=df["Değer"],
            name="Portföy Değeri",
            mode="lines",
            line={"color": SERIES[0], "width": 2},
            hovertemplate=f"%{{x|%d.%m.%Y}}<br>Değer: {currency_symbol}%{{y:,.2f}}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["Tarih"],
            y=df["Maliyet"],
            name="Maliyet",
            mode="lines",
            line={"color": NEUTRAL, "width": 2, "dash": "dot"},
            hovertemplate=f"Maliyet: {currency_symbol}%{{y:,.2f}}<extra></extra>",
        )
    )
    fig.update_layout(hovermode="x unified")
    return _layout(fig)


def dividend_bars(monthly: pd.DataFrame, currency_symbol: str) -> go.Figure:
    fig = go.Figure()
    for kind in (EventKind.PAID, EventKind.EXPECTED, EventKind.PROJECTED):
        label = kind.label
        if label not in monthly or not monthly[label].any():
            continue
        fig.add_trace(
            go.Bar(
                x=monthly["Ay"],
                y=monthly[label],
                name=label,
                marker={"color": EVENT_COLORS[label], "line": {"color": SURFACE, "width": 2}, "cornerradius": 4},
                hovertemplate=f"%{{x}}<br>{label}: {currency_symbol}%{{y:,.2f}}<extra></extra>",
            )
        )
    fig.update_layout(barmode="stack", bargap=0.25, hovermode="x unified")
    fig.update_xaxes(type="category")
    return _layout(fig)


def purpose_vs_target(df: pd.DataFrame) -> go.Figure:
    """Amaç başına gerçekleşen pay (renkli çubuk) ve hedef (çizgi işareti)."""
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=df["Amaç"],
            x=df["Pay %"],
            orientation="h",
            name="Şu anki pay",
            marker={"color": [PURPOSE_COLORS.get(a, NEUTRAL) for a in df["Amaç"]], "cornerradius": 4},
            hovertemplate="<b>%{y}</b><br>Pay (yüzde): %{x:.1f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            y=df["Amaç"],
            x=df["Hedef %"],
            mode="markers",
            name="Hedef",
            marker={"symbol": "line-ns", "size": 26, "line": {"width": 3, "color": TEXT}},
            hovertemplate="Hedef (yüzde): %{x:.1f}<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(ticksuffix="%", gridcolor=GRID, showgrid=True)
    return _layout(fig, height=60 + 52 * len(df))


def budget_trend(df: pd.DataFrame, currency_symbol: str) -> go.Figure:
    fig = go.Figure()
    for col, color in (("Gelir", SERIES[2]), ("Gider", SERIES[1])):
        fig.add_trace(
            go.Bar(
                x=df["Ay"],
                y=df[col],
                name=col,
                marker={"color": color, "line": {"color": SURFACE, "width": 2}, "cornerradius": 4},
                hovertemplate=f"%{{x}}<br>{col}: {currency_symbol}%{{y:,.2f}}<extra></extra>",
            )
        )
    fig.add_trace(
        go.Scatter(
            x=df["Ay"],
            y=df["Net"],
            name="Net tasarruf",
            mode="lines+markers",
            line={"color": SERIES[0], "width": 2},
            hovertemplate=f"Net: {currency_symbol}%{{y:,.2f}}<extra></extra>",
        )
    )
    fig.update_layout(barmode="group", bargap=0.25, hovermode="x unified")
    fig.update_xaxes(type="category")
    return _layout(fig)


def category_vs_budget(df: pd.DataFrame, currency_symbol: str) -> go.Figure:
    """Gider kategorisi başına harcama; bütçesi olanlarda bütçe işareti, aşanlar vurgulu."""
    over = [b is not None and b == b and a > b for a, b in zip(df["Gerçekleşen"], df["Bütçe"], strict=True)]
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=df["Kategori"],
            x=df["Gerçekleşen"],
            orientation="h",
            name="Harcama",
            marker={"color": [SERIES[7] if o else SERIES[0] for o in over], "cornerradius": 4},
            hovertemplate=f"<b>%{{y}}</b><br>{currency_symbol}%{{x:,.2f}}<extra></extra>",
        )
    )
    budgeted = df[df["Bütçe"].notna()]
    if not budgeted.empty:
        fig.add_trace(
            go.Scatter(
                y=budgeted["Kategori"],
                x=budgeted["Bütçe"],
                mode="markers",
                name="Bütçe",
                marker={"symbol": "line-ns", "size": 22, "line": {"width": 3, "color": TEXT}},
                hovertemplate=f"Bütçe: {currency_symbol}%{{x:,.2f}}<extra></extra>",
            )
        )
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(gridcolor=GRID, showgrid=True)
    return _layout(fig, height=60 + 40 * len(df))
