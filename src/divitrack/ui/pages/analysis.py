"""Analiz: amaç dağılımı, enflasyona göre gerçek getiri, sağlık puanı ve senaryolar."""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from divitrack.core.enums import AssetClass, Currency
from divitrack.core.money import HUNDRED
from divitrack.core.planning import score_grade
from divitrack.ui.components import charts
from divitrack.ui.components.widgets import consume_force_refresh, sidebar
from divitrack.ui.formatting import fmt_money, fmt_pct, fmt_signed_money
from divitrack.ui.state import get_container, show_flashes
from divitrack.ui.tables import purpose_frame, real_returns_frame

NUM = st.column_config.NumberColumn


def render() -> None:
    c = get_container()
    base = sidebar(c)
    show_flashes()
    st.title("Analiz")

    with st.spinner("Hesaplanıyor…"):
        summary = c.portfolio.summary(base, force_refresh=consume_force_refresh())
        if not summary.open_positions:
            st.info("Analiz için önce **Varlıklar & İşlemler** sayfasından pozisyon ekleyin.")
            return
        rules = c.insights.rules()
        real = c.insights.real_returns()
        health = c.insights.health(summary, real, rules)

    tab_alloc, tab_real, tab_score, tab_whatif = st.tabs(
        ["Amaçlara göre dağılım", "Enflasyona göre", "Sağlık puanı", "Ya şöyle olursa?"]
    )
    with tab_alloc:
        _allocation(c, summary, rules)
    with tab_real:
        _real_returns(real)
    with tab_score:
        _health(health)
    with tab_whatif:
        _what_if(c, summary)


def _allocation(c, summary, rules) -> None:  # type: ignore[no-untyped-def]
    rows = c.insights.purpose_allocation(summary, rules)
    df = purpose_frame(rows)
    st.caption(
        f"Her varlığın bir görevi var. Hedefler **Ayarlar & Veri** sayfasında; sapma toleransı "
        f"±{rules.drift_tolerance * HUNDRED:.0f} puan. Hedefe dönmek için satış yerine yeni parayı "
        "**Aylık Plan** sayfasındaki öneriye göre bölün."
    )
    st.plotly_chart(charts.purpose_vs_target(df), width="stretch", config={"displayModeBar": False})
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={
            "Değer": NUM(format="%.2f", help=f"{summary.base_currency.value} cinsinden"),
            "Pay %": NUM(format="%.1f"),
            "Hedef %": NUM(format="%.1f"),
            "Sapma (puan)": NUM(format="%+.1f"),
        },
    )


def _real_returns(real) -> None:  # type: ignore[no-untyped-def]
    if not real.has_cpi:
        st.info(
            "Enflasyona göre getiri için **Ayarlar & Veri > TÜFE** bölümüne aylık endeksi girin. "
            "Aşağıdaki tabloda şimdilik yalnızca nominal getiri var."
        )
    else:
        st.caption(
            f"Her alış, alındığı aydan **{real.cpi_month}** ayına kadarki TÜFE artışıyla bugüne taşınır. "
            '"Bugünkü karşılığı" yatırdığınız paranın bugün etmesi gereken tutardır; güncel değer bunun '
            "üstündeyse enflasyonu yenmişsiniz demektir."
        )
    k1, k2, k3 = st.columns(3)
    k1.metric("Net yatırılan", fmt_money(real.invested, Currency.TRY))
    k2.metric(
        "Enflasyonla bugünkü karşılığı",
        fmt_money(real.invested_real, Currency.TRY),
        help="TL dışı işlemler işlem günü kuruyla TL'ye çevrilir",
    )
    k3.metric(
        "Güncel değer",
        fmt_money(real.value, Currency.TRY),
        delta=None if real.real_pct is None else f"{fmt_pct(real.real_pct, 1, signed=True)} reel",
        help=f"Nominal getiri: {fmt_pct(real.nominal_pct, 1, signed=True)}",
    )
    st.dataframe(
        real_returns_frame(real),
        hide_index=True,
        width="stretch",
        column_config={
            "Yatırılan (TL)": NUM(format="%.2f"),
            "Bugünkü karşılığı (TL)": NUM(format="%.2f"),
            "Güncel değer (TL)": NUM(format="%.2f"),
            "Nominal %": NUM(format="%+.1f"),
            "Reel %": NUM(format="%+.1f"),
            "Reel K/Z (TL)": NUM(format="%+.2f"),
        },
    )
    notes = ["Yeni para yalnızca alış ve bedelli işlemleridir; bedelsiz ve temettüden alış maliyetsizdir."]
    if any(r.approx_fx for r in real.rows):
        notes.append("“≈ güncel”: işlem günü kuru alınamadı, güncel kur kullanıldı.")
    if real.unpriced:
        notes.append("Fiyatı olmayanlar dahil edilmedi: " + ", ".join(real.unpriced))
    st.caption(" ".join(notes))


def _health(health) -> None:  # type: ignore[no-untyped-def]
    score = health.score
    st.metric("Toplam", "—" if score is None else f"{score}/100", delta=score_grade(score), delta_color="off")
    st.caption("Verisi olmayan bileşenler toplama katılmaz; puan kalanların üzerinden 100'e ölçeklenir.")
    for comp in health.components:
        with st.container(border=True):
            left, right = st.columns([3, 1])
            left.markdown(f"**{comp.title}**")
            left.caption(comp.detail)
            if comp.available:
                right.markdown(f"**{comp.points:g} / {comp.max_points:g}**")
                left.progress(float(comp.points / comp.max_points))
            else:
                right.markdown("_veri yok_")
            if comp.tip:
                left.caption(f"→ {comp.tip}")


SHOCK_CLASSES = [AssetClass.BIST_STOCK, AssetClass.US_STOCK, AssetClass.PRECIOUS_METAL, AssetClass.FUND]


def _what_if(c, summary) -> None:  # type: ignore[no-untyped-def]
    base = summary.base_currency
    st.caption(
        "Fiyat değişimlerini ve kur hareketini kaydırarak portföyün nasıl etkileneceğini görün. "
        f"Kur şoku {base.value} dışındaki varlıklara uygulanır (pozitif = döviz değer kazanır)."
    )
    present = {pv.asset.asset_class for pv in summary.open_positions}
    cols = st.columns(2)
    shocks: dict[AssetClass, Decimal] = {}
    for i, ac in enumerate(ac for ac in SHOCK_CLASSES if ac in present):
        value = cols[i % 2].slider(f"{ac.label} fiyatı (%)", -60, 60, 0, step=5, key=f"shock_{ac.value}")
        shocks[ac] = Decimal(value) / HUNDRED
    fx = st.slider("Kur değişimi (%)", -30, 60, 0, step=5, key="shock_fx")
    after = c.insights.scenario(summary, shocks, Decimal(fx) / HUNDRED)
    diff = after - summary.total_value
    k1, k2 = st.columns(2)
    k1.metric("Bugünkü değer", fmt_money(summary.total_value, base))
    k2.metric(
        "Senaryo sonrası",
        fmt_money(after, base),
        delta=f"{fmt_signed_money(diff, base)} ({fmt_pct(diff / summary.total_value * 100, 1, signed=True)})",
    )
