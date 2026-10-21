"""Ana panel: KPI'lar, varlık dağılımı, büyüme grafiği, pozisyon tablosu."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from divitrack.core.money import ZERO
from divitrack.core.planning import score_grade
from divitrack.services.dividend_engine import EventKind
from divitrack.services.snapshot_service import snapshots_to_rows
from divitrack.ui.components import charts
from divitrack.ui.components.widgets import consume_force_refresh, delta_pct, sidebar
from divitrack.ui.formatting import fmt_money, fmt_signed_money
from divitrack.ui.state import get_container, run_action, show_flashes
from divitrack.ui.tables import allocation_frame, positions_frame

NUM = st.column_config.NumberColumn


def render() -> None:
    c = get_container()
    base = sidebar(c)
    show_flashes()
    st.title("Portföy Özeti")

    with st.spinner("Fiyatlar güncelleniyor…"):
        summary = c.portfolio.summary(base, force_refresh=consume_force_refresh())
    if not summary.open_positions:
        st.info(
            "Henüz açık pozisyon yok. **Varlıklar & İşlemler** sayfasından ilk varlığını ekle "
            "veya **Ayarlar & Veri** sayfasından CSV/JSON içe aktar."
        )
        return
    c.snapshots.record(summary)

    # --- 12 aylık temettü beklentisi (raporlama para biriminde, net) ---------
    today = date.today()
    events = c.dividends.events(c.portfolio.positions())
    rates = c.market.fx_rates({e.currency for e in events}, base)
    upcoming_net = sum(
        (
            e.net * rates[e.currency]
            for e in events
            if e.kind is not EventKind.PAID
            and today <= e.pay_date <= today + timedelta(days=365)
            and e.currency in rates
        ),
        ZERO,
    )

    sym = base.symbol
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "Toplam Değer",
        fmt_money(summary.total_value, base),
        delta=f"{fmt_signed_money(summary.day_change, base)} ({delta_pct(summary.day_change_pct)})",
        help="Delta: bugünkü değişim (önceki kapanışa göre)",
    )
    k2.metric("Toplam Maliyet", fmt_money(summary.total_cost, base))
    k3.metric(
        "Gerçekleşmemiş K/Z", fmt_signed_money(summary.unrealized_pnl, base), delta=delta_pct(summary.unrealized_pct)
    )
    k4.metric(
        "12 Ay Tahmini Temettü (net)",
        fmt_money(upcoming_net, base),
        delta=delta_pct(upcoming_net / summary.total_value * 100 if summary.total_value else None),
        delta_color="off",
        help="Delta: portföy temettü verimi (net)",
    )

    if summary.realized_pnl:
        st.caption(f"Gerçekleşen K/Z (satışlardan): **{fmt_signed_money(summary.realized_pnl, base)}**")

    # --- Sağlık puanı ve dikkat edilecekler -------------------------------------
    real = c.insights.real_returns()
    rules = c.insights.rules()
    health = c.insights.health(summary, real, rules)
    c.insights.record_score(health)
    alerts = c.insights.alerts(summary, real, rules)
    left, right = st.columns([1, 3])
    with left:
        score = health.score
        st.metric(
            "Portföy sağlık puanı",
            "—" if score is None else f"{score}/100",
            delta=score_grade(score),
            delta_color="off",
            help="Amaç dağılımı, yoğunlaşma, deneme bütçesi, enflasyon, çeşitlilik ve tasarruf oranından hesaplanır. "
            "Ayrıntılar Analiz sayfasında.",
        )
        if health.potential:
            best = health.potential[0]
            st.caption(f"En çok puan kazandıracak: **{best.title}** (+{best.missing:g})")
    with right:
        st.markdown("**Dikkat edilecekler**")
        if not alerts:
            st.success("Şu an dikkat gerektiren bir durum yok.")
        for alert in alerts[:4]:
            getattr(st, alert.level)(alert.text)
        if len(alerts) > 4:
            with st.expander(f"{len(alerts) - 4} uyarı daha"):
                for alert in alerts[4:]:
                    getattr(st, alert.level)(alert.text)

    # --- Grafikler ----------------------------------------------------------
    left, right = st.columns([2, 3])
    with left:
        st.subheader("Varlık Dağılımı")
        view = st.radio("Görünüm", ["Sınıfa göre", "Varlığa göre"], horizontal=True, label_visibility="collapsed")
        by_class = view == "Sınıfa göre"
        alloc = allocation_frame(summary, by="class" if by_class else "asset")
        st.plotly_chart(
            charts.allocation_donut(alloc, sym, by_class=by_class), width="stretch", config={"displayModeBar": False}
        )
    with right:
        st.subheader("Zaman İçinde Değer")
        snaps = c.snapshots.history(base)
        if len(snaps) >= 2:
            growth = pd.DataFrame(snapshots_to_rows(snaps))
            st.plotly_chart(charts.growth_line(growth, sym), width="stretch", config={"displayModeBar": False})
        else:
            st.info(
                "Değer geçmişi her gün açılışta otomatik kaydedilir. Geçmişi işlem defterin ve "
                "tarihsel fiyatlarla şimdi oluşturabilirsin."
            )
        if st.button(
            "Geçmişi işlemlerden yeniden oluştur",
            disabled=not c.market.online,
            help="İlk işlemden bugüne günlük değeri Yahoo geçmiş fiyatlarıyla hesaplar",
        ):
            with st.spinner("Tarihsel fiyatlar indiriliyor…"):
                run_action(lambda: c.snapshots.backfill(base), "Değer geçmişi oluşturuldu")

    # --- Pozisyonlar --------------------------------------------------------
    st.subheader("Pozisyonlar")
    df = positions_frame(summary)
    value_col = f"Değer ({base.value})"
    st.dataframe(
        df.drop(columns=["Gerçekleşen K/Z"]),
        hide_index=True,
        width="stretch",
        column_config={
            "Ort. Maliyet": NUM(format="%.2f"),
            "Fiyat": NUM(format="%.2f"),
            "Günlük %": NUM(format="%.2f%%"),
            value_col: NUM(format="%.2f"),
            "K/Z": NUM(format="%.2f"),
            "K/Z %": NUM(format="%.2f%%"),
            "Ağırlık %": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
        },
    )
    as_of = [p.quote.as_of for p in summary.open_positions if p.quote and p.quote.as_of]
    if as_of:
        st.caption(f"Son fiyat güncellemesi: {max(as_of):%d.%m.%Y %H:%M} · Fiyatlar gecikmeli olabilir.")
