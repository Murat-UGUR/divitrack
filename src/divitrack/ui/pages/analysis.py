"""Analiz: amaç dağılımı, enflasyona ve dolara göre getiri, alım alım getiri, para akışı,
piyasa araçlarıyla kıyas, sağlık puanı ve senaryolar."""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from divitrack.core.enums import AssetClass, Currency
from divitrack.core.money import HUNDRED
from divitrack.core.planning import score_grade
from divitrack.services.analytics_service import PRESETS
from divitrack.ui.components import charts
from divitrack.ui.components.widgets import consume_force_refresh, sidebar
from divitrack.ui.formatting import fmt_money, fmt_pct, fmt_signed_money
from divitrack.ui.state import get_container, show_flashes
from divitrack.ui.tables import (
    asset_returns_frame,
    benchmark_frame,
    contributions_frame,
    lots_frame,
    purpose_frame,
    real_returns_frame,
    report_card_frame,
    scenario_frame,
    score_history_frame,
)

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

    tab_alloc, tab_cards, tab_real, tab_lots, tab_flow, tab_bench, tab_score, tab_whatif = st.tabs(
        [
            "Amaçlara göre dağılım",
            "Varlık karnesi",
            "Enflasyona göre",
            "Alım alım getiri",
            "Para akışı",
            "Kıyas",
            "Sağlık puanı",
            "Ya şöyle olursa?",
        ]
    )
    with tab_alloc:
        _allocation(c, summary, rules)
    with tab_real:
        _real_returns(real)
    with tab_cards:
        _report_cards(c, summary, real, rules)
    with tab_lots:
        _lots(c)
    with tab_flow:
        _contributions(c)
    with tab_bench:
        _benchmarks(c)
    with tab_score:
        _health(health)
        history = c.insights.score_history()
        if len(history) >= 2:
            st.markdown("**Puanın seyri**")
            st.plotly_chart(
                charts.score_line(score_history_frame(history)), width="stretch", config={"displayModeBar": False}
            )
        else:
            st.caption("Puan her ay Portföy Özeti açıldığında kaydedilir; iki aydan sonra seyri burada görünür.")
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


def _report_cards(c, summary, real, rules) -> None:  # type: ignore[no-untyped-def]
    pairs = [(pv.asset, pv.state) for pv in summary.open_positions]
    quotes = {pv.asset.id: pv.quote for pv in summary.open_positions}
    yields = {
        a.id: c.dividends.yield_info(a, s, quotes.get(a.id)).current_yield
        for a, s in pairs
        if a.asset_class in (AssetClass.BIST_STOCK, AssetClass.US_STOCK, AssetClass.FUND)
    }
    cards = c.insights.report_cards(summary, real, yields, rules)
    st.caption(
        'Her kalem için ağırlık, getiri ve temettü verimi; kurallarınıza takılan durumlar "İşaretler" '
        "kolonunda. İşaretsiz kalem **İyi**, bir işaret **İzle**, iki ve üstü **Dikkat**."
    )
    st.dataframe(
        report_card_frame(cards),
        hide_index=True,
        width="stretch",
        column_config={
            "Ağırlık %": NUM(format="%.1f"),
            "K/Z %": NUM(format="%+.1f"),
            "Reel %": NUM(format="%+.1f"),
            "Temettü verimi %": NUM(format="%.2f"),
        },
    )


SHOCK_CLASSES = [AssetClass.BIST_STOCK, AssetClass.US_STOCK, AssetClass.PRECIOUS_METAL, AssetClass.FUND]


def _what_if(c, summary) -> None:  # type: ignore[no-untyped-def]
    base = summary.base_currency
    st.caption(
        "Fiyat değişimlerini ve kur hareketini kaydırarak portföyün nasıl etkileneceğini görün. "
        f"Kur şoku {base.value} dışındaki varlıklara uygulanır (pozitif = döviz değer kazanır)."
    )
    present = {pv.asset.asset_class for pv in summary.open_positions}
    presets = {p.key: p for p in PRESETS}
    choice = st.radio(
        "Hazır senaryo",
        ["custom", *presets],
        format_func=lambda k: "Kendi senaryom" if k == "custom" else presets[k].title,
        horizontal=True,
        key="scenario_preset",
    )
    shocks: dict[AssetClass, Decimal] = {}
    if choice == "custom":
        cols = st.columns(2)
        for i, ac in enumerate(ac for ac in SHOCK_CLASSES if ac in present):
            value = cols[i % 2].slider(f"{ac.label} fiyatı (%)", -60, 60, 0, step=5, key=f"shock_{ac.value}")
            shocks[ac] = Decimal(value) / HUNDRED
        fx_shock = Decimal(st.slider("Kur değişimi (%)", -30, 60, 0, step=5, key="shock_fx")) / HUNDRED
    else:
        preset = presets[choice]
        shocks = dict(preset.class_shocks)
        fx_shock = preset.fx_shock
        parts = [f"{ac.label} {fmt_pct(v * HUNDRED, 0, signed=True)}" for ac, v in shocks.items()]
        if fx_shock:
            parts.append(f"kur {fmt_pct(fx_shock * HUNDRED, 0, signed=True)}")
        st.caption(f"{preset.description} Varsayım: {', '.join(parts)}.")
    after = c.insights.scenario(summary, shocks, fx_shock)
    diff = after - summary.total_value
    k1, k2 = st.columns(2)
    k1.metric("Bugünkü değer", fmt_money(summary.total_value, base))
    k2.metric(
        "Senaryo sonrası",
        fmt_money(after, base),
        delta=f"{fmt_signed_money(diff, base)} ({fmt_pct(diff / summary.total_value * 100, 1, signed=True)})",
    )
    impact = c.analytics.scenario_by_purpose(summary, shocks, fx_shock)
    if impact:
        st.markdown("**Amaçlara etkisi**")
        st.dataframe(
            scenario_frame(impact),
            hide_index=True,
            width="stretch",
            column_config={
                "Bugün": NUM(format="%.2f", help=f"{base.value} cinsinden"),
                "Senaryoda": NUM(format="%.2f"),
                "Fark": NUM(format="%+.2f"),
                "Fark %": NUM(format="%+.1f"),
            },
        )
        st.caption(
            "Koruma amaçlı varlıklar kötü senaryoda değer kaybını sınırlıyorsa görevini yapıyor demektir; "
            "kur şoku yalnızca raporlama para birimi dışındaki varlıklara uygulanır."
        )


def _lots(c) -> None:  # type: ignore[no-untyped-def]
    report = c.analytics.lots()
    if not report.lots:
        st.info("Elde kalan alım yok.")
        return
    st.caption(
        "Her alış ayrı bir lot olarak izlenir; satışlar en eski lottan düşülür (FIFO), bedelsiz eldeki "
        "lotların adedini artırır. **Reel**: alış tutarı TÜFE ile bugüne taşınarak; **dolar bazında**: alış "
        "günü ve bugünkü USD/TL kuruyla karşılaştırılır. Kur yükselişi TL getiriyi şişirebilir; dolar bazındaki "
        "getiri bunu ayıklar."
    )
    total = report.total()
    if total is not None:
        k1, k2, k3 = st.columns(3)
        k1.metric("Nominal (TL)", fmt_pct(total.nominal_pct, 1, signed=True))
        k2.metric(
            "Reel (TÜFE)",
            fmt_pct(total.real_pct, 1, signed=True),
            help=f"TÜFE son ay: {report.cpi_month}" if report.cpi_month else "TÜFE verisi yok",
        )
        k3.metric(
            "Dolar bazında",
            fmt_pct(total.usd_pct, 1, signed=True),
            help=f"Bugünkü USD/TL: {fmt_money(report.usd_rate, Currency.TRY, 4)}" if report.usd_rate else None,
        )
    pct_cols = {k: NUM(format="%+.1f") for k in ("Nominal %", "Reel %", "Dolar bazında %")}
    st.markdown("**Kalem bazında**")
    st.dataframe(
        asset_returns_frame(report),
        hide_index=True,
        width="stretch",
        column_config={"Maliyet (TL)": NUM(format="%.2f"), "Değer (TL)": NUM(format="%.2f"), **pct_cols},
    )
    st.markdown("**Alım alım**")
    st.dataframe(
        lots_frame(report),
        hide_index=True,
        width="stretch",
        column_config={
            "Tarih": st.column_config.DateColumn(format="DD.MM.YYYY"),
            "Kalan adet": NUM(format="%.4f"),
            "Alış (TL)": NUM(format="%.2f"),
            "Bugün (TL)": NUM(format="%.2f"),
            "Enflasyonla alış (TL)": NUM(format="%.2f"),
            **pct_cols,
        },
    )
    notes = []
    if any(lot.approx_fx for lot in report.lots):
        notes.append("“≈ güncel”: alış günü kuru alınamadı, güncel kur kullanıldı.")
    if report.unpriced:
        notes.append("Fiyatı olmayanların bugünkü değeri boş: " + ", ".join(report.unpriced))
    if notes:
        st.caption(" ".join(notes))


def _contributions(c) -> None:  # type: ignore[no-untyped-def]
    rows = c.analytics.contributions()
    if not rows:
        st.info("Henüz alış kaydı yok.")
        return
    df = contributions_frame(rows)
    st.caption(
        "Hangi ay ne kadar yeni para yatırdığın (alış ve bedelli; satışlar düşülür) ve birikimli tutarın "
        "TÜFE ile bugüne taşınmış hali. Portföy değeri turuncu çizginin üstündeyse birikimin enflasyonu yeniyor."
    )
    last = rows[-1]
    k1, k2, k3 = st.columns(3)
    k1.metric("Toplam net yatırılan", fmt_money(last.cumulative, Currency.TRY))
    k2.metric("Enflasyonla bugünkü karşılığı", fmt_money(last.cumulative_real, Currency.TRY))
    active = [r for r in rows if r.invested > 0]
    k3.metric("Yatırım yapılan ay", f"{len(active)} / {len(rows)}")
    st.plotly_chart(charts.contributions_chart(df, "₺"), width="stretch", config={"displayModeBar": False})
    with st.expander("Ay ay tablo"):
        st.dataframe(
            df.iloc[::-1],
            hide_index=True,
            width="stretch",
            column_config={col: NUM(format="%.2f") for col in df.columns if col != "Ay"},
        )


def _benchmarks(c) -> None:  # type: ignore[no-untyped-def]
    annual = c.analytics.annual_return()
    st.caption(
        "Portföyünün **para ağırlıklı yıllık getirisi** (XIRR: her alışın tarihi ve tutarı hesaba katılır) ile "
        "kendi girdiğin araçların yıllık getirisi. Reel getiri son 12 ayın TÜFE'siyle hesaplanır. Araçları "
        "**Ayarlar & Veri > Kıyas araçları** bölümünden ekle (ör. mevduat, BIST 100, altın, dolar)."
    )
    k1, k2, k3 = st.columns(3)
    k1.metric("Portföy (yıllık, nominal)", fmt_pct(annual.nominal_pct, 1, signed=True))
    k2.metric("Portföy (yıllık, reel)", fmt_pct(annual.real_pct, 1, signed=True))
    k3.metric("TÜFE (son 12 ay)", fmt_pct(annual.cpi_12m_pct, 1))
    if annual.nominal_pct is None:
        st.caption("Yıllık getiri için en az bir aylık işlem geçmişi ve tüm açık pozisyonların fiyatı gerekir.")
    rows = c.analytics.benchmark_table(annual)
    if len(rows) > 1:
        st.dataframe(
            benchmark_frame(rows),
            hide_index=True,
            width="stretch",
            column_config={"Nominal %": NUM(format="%+.1f"), "Reel %": NUM(format="%+.1f")},
        )
    else:
        st.info("Kıyas için henüz araç eklenmemiş.")
