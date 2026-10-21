"""Temettüler: verim tablosu, aylık takvim grafiği, kayıtlar."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from divitrack.core.entities import Dividend
from divitrack.core.enums import AssetClass, Currency, DividendStatus
from divitrack.core.money import to_decimal
from divitrack.services.dividend_engine import EventKind
from divitrack.ui.components import charts
from divitrack.ui.components.widgets import sidebar
from divitrack.ui.formatting import fmt_money, fmt_pct
from divitrack.ui.state import get_container, run_action, show_flashes
from divitrack.ui.tables import dividends_frame, events_frame, monthly_income, yields_frame

NUM = st.column_config.NumberColumn
DIVIDEND_CLASSES = (AssetClass.BIST_STOCK, AssetClass.US_STOCK)


def render() -> None:
    c = get_container()
    base = sidebar(c)
    show_flashes()
    st.title("Temettüler")

    today = date.today()
    pairs = c.portfolio.positions()
    assets = {a.id: a for a, _ in pairs if a.id is not None}
    held = [(a, s) for a, s in pairs if s.is_open and a.asset_class in DIVIDEND_CLASSES]

    with st.spinner("Temettü geçmişi yükleniyor…"):
        events = c.dividends.events(pairs)
        quotes = c.market.quotes_for([a for a, _ in held])
        infos = [c.dividends.yield_info(a, s, quotes.get(a.id or -1)) for a, s in held]
    rates = c.market.fx_rates({e.currency for e in events} | {a.currency for a, _ in held}, base)
    ev = events_frame(events, rates, base)
    net_col = f"Net ({base.value})"

    # --- KPI'lar ---------------------------------------------------------------
    def total(kinds: set[EventKind], start: date, end: date) -> float:
        if ev.empty:
            return 0.0
        mask = ev["_kind"].isin([k.value for k in kinds]) & (ev["Tarih"] >= start) & (ev["Tarih"] <= end)
        return float(ev.loc[mask, net_col].fillna(0).sum())

    received_ttm = total({EventKind.PAID}, today - timedelta(days=365), today)
    received_ytd = total({EventKind.PAID}, date(today.year, 1, 1), today)
    upcoming = total({EventKind.EXPECTED, EventKind.PROJECTED}, today, today + timedelta(days=365))
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Bu yıl alınan (net)", fmt_money(received_ytd, base))
    k2.metric("Son 12 ay alınan (net)", fmt_money(received_ttm, base))
    k3.metric("Gelecek 12 ay (net, tahmini)", fmt_money(upcoming, base))
    k4.metric("Aylık ortalama (tahmini)", fmt_money(upcoming / 12, base))

    # --- Takvim ----------------------------------------------------------------
    st.subheader("Aylık Temettü Takvimi")
    m = today.year * 12 + today.month - 1 - 11  # 11 ay geri + bu ay + 12 ay ileri
    start = date(m // 12, m % 12 + 1, 1)
    monthly = monthly_income(ev, base, start, months=24)
    if monthly.drop(columns=["Ay"]).to_numpy().sum() > 0:
        st.plotly_chart(charts.dividend_bars(monthly, base.symbol), width="stretch", config={"displayModeBar": False})
        st.caption(
            "Ödendi: kayıtlı ödemeler · Beklenen: sizin girdiğiniz · Tahmini: geçen yılın "
            "takvimi ve tutarı tekrarlanır (Yahoo Finance verisi). Tutarlar stopaj sonrası nettir."
        )
    else:
        st.info("Gösterilecek temettü yok. Temettü ödeyen hisse ekleyin veya aşağıdan kayıt girin.")

    # --- Verim tablosu -----------------------------------------------------------
    st.subheader("Verim")
    if infos:
        st.dataframe(
            yields_frame(infos, assets),
            hide_index=True,
            width="stretch",
            column_config={
                "TTM Temettü/Hisse": NUM(format="%.4f"),
                "Temettü Verimi %": NUM(format="%.2f%%"),
                "Maliyete Göre Verim %": NUM(format="%.2f%%"),
                "Yıllık Brüt": NUM(format="%.2f"),
                "Yıllık Net": NUM(format="%.2f"),
            },
        )
        st.caption("TTM: son 12 ayda dağıtılan hisse başı brüt temettü. Yıllık tutarlar varlığın para birimindedir.")
    else:
        st.caption("Açık hisse pozisyonu yok.")

    _declaration(today.year)

    # --- Kayıtlar ----------------------------------------------------------------
    tab_add, tab_list, tab_import = st.tabs(["Temettü Kaydı Ekle", "Kayıtlar", "Geçmişten Aktar"])
    with tab_add:
        _add_form(held or pairs)
    with tab_list:
        divs = c.dividends.dividends.list()
        if not divs:
            st.info("Kayıtlı temettü yok.")
        else:
            df = dividends_frame(divs, assets)
            st.dataframe(
                df,
                hide_index=True,
                width="stretch",
                column_config={
                    "Ödeme": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Hak Kullanım": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Stopaj %": NUM(format="%.1f"),
                },
            )
            col1, col2 = st.columns(2)
            pick = col1.selectbox(
                "Kayıt",
                df["ID"].tolist(),
                format_func=lambda i: (
                    f"#{i} · {df.loc[df['ID'] == i, 'Sembol'].iloc[0]} · "
                    f"{df.loc[df['ID'] == i, 'Ödeme'].iloc[0]:%d.%m.%Y}"
                ),
            )
            if col2.button("Ödendi olarak işaretle"):
                run_action(lambda: c.dividends.mark_paid(int(pick)), "Güncellendi")
            if col2.button("Kaydı sil"):
                run_action(lambda: c.dividends.delete(int(pick)), "Silindi")
    with tab_import:
        _import_history(held)


def _add_form(pairs: list) -> None:  # type: ignore[type-arg]
    c = get_container()
    options = [a for a, _ in pairs]
    if not options:
        st.info("Önce hisse pozisyonu ekleyin.")
        return
    qty_by_id = {a.id: s.quantity for a, s in pairs}
    with st.form("div_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        asset = col1.selectbox("Varlık", options, format_func=lambda a: a.display_name)
        status = col2.radio("Durum", list(DividendStatus), format_func=lambda s: s.label, horizontal=True)
        pay_date = col3.date_input("Ödeme tarihi", value=date.today(), format="DD.MM.YYYY")
        per_share = col1.text_input("Hisse başı brüt temettü")
        qty = col2.text_input("Adet (boş = mevcut adet)")
        tax = col3.text_input("Stopaj oranı % (boş = varsayılan)")
        note = st.text_input("Not")
        if st.form_submit_button("Kaydet", type="primary"):

            def add() -> Dividend:
                rate = (
                    to_decimal(tax, field="Stopaj") / 100
                    if tax.strip()
                    else c.dividends.withholding_rate(asset.asset_class)
                )
                assert asset.id is not None
                return c.dividends.record(
                    Dividend(
                        asset_id=asset.id,
                        pay_date=pay_date,
                        status=status,
                        currency=asset.currency,
                        amount_per_share=to_decimal(per_share, field="Hisse başı temettü"),
                        quantity=to_decimal(qty, field="Adet") if qty.strip() else qty_by_id[asset.id],
                        withholding_rate=rate,
                        note=note,
                    )
                )

            run_action(add, "Temettü kaydedildi")


def _import_history(held: list) -> None:  # type: ignore[type-arg]
    c = get_container()
    st.write(
        "Yahoo Finance temettü geçmişinden, hisseyi elde tuttuğunuz döneme denk gelen ödemeleri "
        "'Ödendi' kaydı olarak ekler. Adet, hak kullanım tarihindeki pozisyonunuzdan hesaplanır."
    )
    if not held or not c.market.online:
        st.caption("Açık hisse pozisyonu yok veya çevrimdışı moddasınız.")
        return
    suggestions = []
    for a, _ in held:
        suggestions += c.dividends.suggest_from_history(a, c.portfolio.transactions.list(a.id))
    if not suggestions:
        st.success("Eklenecek yeni ödeme bulunamadı.")
        return
    st.write(f"**{len(suggestions)}** ödeme bulundu.")
    if st.button("Hepsini ekle", type="primary"):

        def add_all() -> None:
            for d in suggestions:
                c.dividends.record(d)

        run_action(add_all, f"{len(suggestions)} temettü kaydı eklendi")


def _declaration(year: int) -> None:
    """Yurt dışı temettülerin yıllık beyan sınırına göre durumu."""
    c = get_container()
    st.subheader(f"Yurt dışı temettü ve beyan sınırı ({year})")
    status = c.insights.declaration_status(year)
    col1, col2, col3 = st.columns(3)
    col1.metric("Bu yıl ödenen (brüt, TL)", fmt_money(status.gross_try, Currency.TRY))
    col2.metric("Beyan sınırı", fmt_money(status.limit, Currency.TRY) if status.limit else "Girilmedi")
    col3.metric("Doluluk", fmt_pct(status.ratio * 100 if status.ratio is not None else None, 0))
    if status.ratio is not None:
        st.progress(min(float(status.ratio), 1.0))
    if status.exceeded:
        st.error("Sınır aşıldı: bu yılın yurt dışı temettüleri için gelir vergisi beyannamesi gerekebilir.")
    if not status.limit:
        st.caption("Sınırı **Ayarlar & Veri > Hedefler ve kurallar** bölümünden girin; her yıl güncellenir.")
    st.caption(
        "Yalnızca **Ödendi** işaretli, TL dışındaki temettüler sayılır. Brüt tutar ödeme günü kuruyla "
        "TL'ye çevrilir"
        + (" (bazı kayıtlarda kur geçmişi yok, güncel kur kullanıldı)" if status.approx_fx else "")
        + ". Resmi hesap TCMB kuruyla yapılır; bu bir ön izlemedir, vergi tavsiyesi değildir."
    )
