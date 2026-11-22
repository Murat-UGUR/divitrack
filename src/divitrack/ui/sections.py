"""0.8 arayüz bölümleri: ne değişti, risk, vergi ön izleme, alım planı, pasif gelir, hızlı giriş, döküm aktarma,
alarmlar, bildirimler, güvenlik ve beklentiler. Sayfalar bu fonksiyonları çağırır."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import streamlit as st

from divitrack.core.broker import FIELD_LABELS, FIELDS
from divitrack.core.entities import Alert
from divitrack.core.enums import AlertKind, AssetClass, Currency
from divitrack.core.exceptions import DivitrackError
from divitrack.core.gold_plan import BuyPlan
from divitrack.core.money import ZERO, to_decimal
from divitrack.services.io.broker_import import PRESET_NAMES
from divitrack.ui.formatting import fmt_money, fmt_pct, fmt_qty, fmt_signed_money
from divitrack.ui.state import flash, run_action

NUM = st.column_config.NumberColumn
TRY = Currency.TRY


def _dec(raw: str, field: str) -> Decimal | None:
    return to_decimal(raw, field=field) if raw.strip() else None


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


# =========================================================================== Analiz
def changes(c, base: Currency) -> None:  # type: ignore[no-untyped-def]
    dates = c.snapshots.snapshot_dates(base)
    st.caption(
        "Portföy Özeti her açıldığında o günün varlık bazında değerini kaydeder. Burada iki kaydı karşılaştırıp "
        "farkın **fiyat hareketinden** mi, **yeni alım / satış / temettüden** mi geldiğini görürsün."
    )
    if len(dates) < 2:
        st.info("Karşılaştırma için en az iki farklı günde kayıt gerekiyor. Portföy Özeti'ni başka bir gün tekrar aç.")
        return
    earlier = list(reversed(dates[:-1]))
    since = st.selectbox("Karşılaştır", earlier, format_func=lambda d: d.strftime("%d.%m.%Y"), key="chg_since")
    rep = c.snapshots.changes(base, since)
    if rep is None or not rep.rows:
        st.info("Seçilen kayıtlarda karşılaştırılacak varlık yok.")
        return
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Toplam fark", fmt_signed_money(rep.total_change, base), help=f"{since:%d.%m.%Y} → {rep.until:%d.%m.%Y}")
    k2.metric("Fiyat hareketlerinden", fmt_signed_money(rep.price_effect, base))
    k3.metric("Yeni alım / satıştan", fmt_signed_money(rep.flow_effect, base))
    k4.metric("Bu sürede yatırılan", fmt_signed_money(rep.cost_added, base))
    df = pd.DataFrame(
        [
            {
                "Sembol": r.asset.symbol,
                "Önceki değer": _f(r.value_before),
                "Şimdiki değer": _f(r.value_after),
                "Fark": _f(r.change),
                "Fiyat etkisi": _f(r.price_effect),
                "Alım/satış etkisi": _f(r.flow_effect),
                "Fiyat değişimi %": _f(r.price_change_pct),
            }
            for r in rep.rows
        ]
    )
    money = {
        k: NUM(format="%.0f") for k in ("Önceki değer", "Şimdiki değer", "Fark", "Fiyat etkisi", "Alım/satış etkisi")
    }
    st.dataframe(df, hide_index=True, width="stretch", column_config={**money, "Fiyat değişimi %": NUM(format="%+.1f")})
    st.bar_chart(df.set_index("Sembol")[["Fiyat etkisi", "Alım/satış etkisi"]], horizontal=True, stack=True)


def risk(c, base: Currency) -> None:  # type: ignore[no-untyped-def]
    with st.spinner("Fiyat geçmişi alınıyor…"):
        rep = c.risk.report(base)
    st.caption(
        "Zirveden düşüş, yatırdığın ve çektiğin paradan arındırılmış performans endeksiyle hesaplanır (alım yapmak "
        "yükseliş, satış düşüş sayılmaz). Oynaklık son 1 yılın günlük TL getirilerinden yıllıklandırılır."
    )
    dd = rep.drawdown
    k1, k2, k3 = st.columns(3)
    k1.metric(
        "En büyük düşüş",
        fmt_pct(dd.max_pct, 1) if dd else "—",
        help=f"Zirve {dd.peak:%d.%m.%Y} → dip {dd.trough:%d.%m.%Y}" if dd and dd.peak and dd.trough else None,
    )
    k2.metric("Bugün zirveye göre", fmt_pct(dd.current_pct, 1) if dd else "—")
    k3.metric("Portföy oynaklığı (yıllık)", fmt_pct(rep.portfolio_volatility_pct, 1))
    if len(rep.index) >= 2:
        st.line_chart(pd.DataFrame(rep.index, columns=["Tarih", "Performans (ilk gün 100)"]).set_index("Tarih"))
    else:
        st.info("Değer geçmişi yok. Portföy Özeti'nde *Geçmişi işlemlerden yeniden oluştur* ile doldurabilirsin.")
    if rep.assets:
        st.markdown("**Kalemler**")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Sembol": r.asset.symbol, "Pay %": _f(r.weight_pct), "Yıllık oynaklık %": _f(r.volatility_pct)}
                    for r in rep.assets
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={"Pay %": NUM(format="%.1f"), "Yıllık oynaklık %": NUM(format="%.1f")},
        )
        symbols = [r.asset.symbol for r in rep.assets if r.volatility_pct is not None]
        if len(symbols) >= 2:
            st.markdown("**Birlikte hareket (korelasyon)**")
            st.caption(
                "1'e yakın: birlikte yükselip düşüyor · 0 civarı: bağımsız · eksi: biri düşerken diğeri yükseliyor."
            )
            matrix = pd.DataFrame(
                [[_f(rep.correlation(a, b)) for b in symbols] for a in symbols], index=symbols, columns=symbols
            )
            st.dataframe(matrix, width="stretch", column_config={s: NUM(format="%.2f") for s in symbols})


def tax(c) -> None:  # type: ignore[no-untyped-def]
    st.caption(
        "ABD hisse/ETF satışlarından doğan kazancın TL karşılığı. Maliyet, alış ayından önceki ay ile satış ayından "
        "önceki ay arasındaki **Yİ-ÜFE** artışı %10 veya fazlaysa bu oranda artırılır. Kurlar alış ve satış "
        "günlerinin USD/TL kurudur. Yİ-ÜFE endeksini *Ayarlar & Veri > Yİ-ÜFE* bölümüne gir. "
        "**Ön izlemedir, vergi tavsiyesi değildir**; beyan için mali müşavirine danış."
    )
    years = c.tax.years_with_sales()
    if not years:
        st.info("Yurt dışı hisse/ETF satışı yok.")
        return
    year = st.selectbox("Yıl", years, key="tax_year")
    p = c.tax.foreign_sales(year)
    k1, k2, k3 = st.columns(3)
    k1.metric("Net kazanç (TL)", fmt_signed_money(p.net_gain, TRY))
    k2.metric("İstisna", fmt_money(p.exemption, TRY) if p.exemption else "girilmedi")
    k3.metric("Beyana esas (tahmini)", fmt_money(p.taxable, TRY))
    if p.ppi_missing:
        st.warning("Bazı satışlar için Yİ-ÜFE endeksi eksik; o satışlarda endeksleme yapılmadı.")
    if p.missing_fx:
        st.warning("Kur geçmişi alınamadığı için atlananlar: " + ", ".join(p.missing_fx))
    rows = [
        {
            "Sembol": r.asset.symbol,
            "Alış": g.buy_date,
            "Satış": g.sell_date,
            "Adet": float(g.quantity),
            "Maliyet (TL)": float(g.cost_try),
            "Endeksli maliyet (TL)": float(g.indexed_cost_try),
            "Satış (TL)": float(g.proceeds_try),
            "Kazanç (TL)": float(g.gain_try),
            "Endeksleme": "✓" if g.indexed else "",
        }
        for r in p.rows
        for g in r.gains
    ]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            **{k: NUM(format="%.2f") for k in ("Maliyet (TL)", "Endeksli maliyet (TL)", "Satış (TL)", "Kazanç (TL)")},
            "Alış": st.column_config.DateColumn(format="DD.MM.YYYY"),
            "Satış": st.column_config.DateColumn(format="DD.MM.YYYY"),
        },
    )


# =========================================================================== Aylık Plan
def buy_plan(c) -> None:  # type: ignore[no-untyped-def]
    st.subheader("Düzenli alım planı")
    st.caption(
        "Her ay belli bir miktar almayı planla (ör. 10 gram altın). Ay ay plan ile gerçekleşeni, ortalama maliyetin "
        "seyrini ve fiyat belirlediğin eşiğin altına inerse önerilen ek alımı gösterir."
    )
    plan = c.plans.buy_plan()
    assets = c.portfolio.assets.list()
    with st.expander("Planı düzenle", expanded=plan is None):
        symbols = [a.symbol for a in assets] or ["XAU-GRAM"]
        default = (
            symbols.index(plan.symbol)
            if plan and plan.symbol in symbols
            else (symbols.index("XAU-GRAM") if "XAU-GRAM" in symbols else 0)
        )
        with st.form("buy_plan"):
            col1, col2, col3 = st.columns(3)
            symbol = col1.selectbox("Varlık", symbols, index=default)
            qty = col2.text_input("Aylık miktar", value=fmt_qty(plan.monthly_quantity) if plan else "10")
            start = col3.text_input(
                "Başlangıç ayı (YYYY-AA)", value=plan.start_month if plan else date.today().strftime("%Y-%m")
            )
            col4, col5 = st.columns(2)
            dip = col4.text_input(
                "Ek alım eşiği (fiyat, isteğe bağlı)",
                value=fmt_qty(plan.dip_price) if plan and plan.dip_price else "",
                help="Fiyat bu seviyenin altındaysa o ay ek alım önerilir",
            )
            extra = col5.text_input("Eşik altında ek miktar", value=fmt_qty(plan.dip_extra) if plan else "0")
            save, remove = st.columns(2)
            if save.form_submit_button("Planı kaydet", type="primary"):
                run_action(
                    lambda: c.plans.save_buy_plan(
                        BuyPlan(
                            symbol,
                            to_decimal(qty, field="Aylık miktar"),
                            start.strip(),
                            _dec(dip, "Ek alım eşiği"),
                            _dec(extra, "Ek miktar") or ZERO,
                        )
                    ),
                    "Alım planı kaydedildi",
                )
            if plan is not None and remove.form_submit_button("Planı kaldır"):
                run_action(lambda: c.plans.save_buy_plan(None), "Alım planı kaldırıldı")
    if plan is None:
        return
    try:
        res = c.plans.buy_plan_status()
    except DivitrackError as exc:
        st.warning(str(exc))
        return
    if res is None:
        return
    plan, asset, status = res
    unit = asset.currency
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Bu ay kalan", fmt_qty(status.this_month_remaining))
    k2.metric(
        "Önerilen alım",
        fmt_qty(status.suggested),
        help=f"≈ {fmt_money(status.suggested_amount, unit)}" if status.suggested_amount is not None else None,
    )
    k3.metric("Plana göre eksik" if status.behind >= 0 else "Plandan fazla", fmt_qty(abs(status.behind)))
    k4.metric("Güncel fiyat", fmt_money(status.price_now, unit))
    if status.dip_active:
        st.success(
            f"Fiyat ek alım eşiğinin ({fmt_money(plan.dip_price, unit)}) altında: "
            f"bu ay {fmt_qty(plan.dip_extra)} ek alım önerilir."
        )
    if status.months:
        df = pd.DataFrame(
            [
                {
                    "Ay": m.month,
                    "Plan": float(m.planned),
                    "Alınan": float(m.bought),
                    "Harcanan": float(m.spent),
                    "Birikimli plan": float(m.cum_planned),
                    "Birikimli alınan": float(m.cum_bought),
                    "Ay sonu ortalama": _f(m.average_after),
                    "Durum": "✅" if m.done else "⏳",
                }
                for m in status.months
            ]
        )
        st.dataframe(
            df,
            hide_index=True,
            width="stretch",
            column_config={"Harcanan": NUM(format="%.0f"), "Ay sonu ortalama": NUM(format="%.2f")},
        )
        if df["Ay sonu ortalama"].notna().sum() >= 2:
            st.line_chart(df.set_index("Ay")[["Ay sonu ortalama"]])


def passive_income(c) -> None:  # type: ignore[no-untyped-def]
    st.subheader("Pasif gelir hedefi")
    target = c.plans.passive_target()
    with st.form("passive_target"):
        raw = st.text_input(
            "Temettüden aylık hedef (TL, net)",
            value=fmt_qty(target) if target else "",
            help="Boş bırakırsan hedef kaldırılır",
        )
        if st.form_submit_button("Kaydet"):
            run_action(lambda: c.plans.set_passive_target(_dec(raw, "Aylık hedef")), "Pasif gelir hedefi kaydedildi")
    inc = c.plans.passive_income()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Aylık ortalama (önümüzdeki 12 ay)", fmt_money(inc.monthly, TRY))
    k2.metric("Hedefe ulaşma", fmt_pct(inc.progress_pct, 0))
    k3.metric("Temettü ödeyenlerin net verimi", fmt_pct(inc.yield_pct, 1))
    k4.metric(
        "Hedef için gereken ek yatırım",
        fmt_money(inc.capital_needed, TRY, 0),
        help="Kalan aylık açığı bugünkü net verimle kapatmak için",
    )
    if inc.progress_pct is not None:
        st.progress(min(1.0, float(inc.progress_pct) / 100))
    st.caption(
        "Beklenen temettüler girdiğin kayıtlardan ve geçmiş ödemelerden tahmin edilir (Temettüler sayfası); "
        "stopaj düşülmüş, bugünkü kurla TL'ye çevrilmiştir."
    )


# =========================================================================== Varlıklar
def quick_entry(c) -> None:  # type: ignore[no-untyped-def]
    st.markdown("#### Hızlı giriş")
    st.caption(
        "Tek satır yaz: `5 gr altın 5750` · `sat 100 EREGL 45,5` · `12 SCHD 31.5$ 18.11.2026` · "
        "`40,5 gr gümüş 75,40 dün`. "
        "Kaydetmeden önce önizleme gösterilir."
    )
    with st.form("quick_entry"):
        text = st.text_input("İşlem", placeholder="5 gr altın 5750")
        preview = st.form_submit_button("Önizle")
    if preview and text.strip():
        try:
            st.session_state["_quick_pending"] = c.quick.prepare(text)
        except DivitrackError as exc:
            st.session_state.pop("_quick_pending", None)
            st.error(str(exc))
    entry = st.session_state.get("_quick_pending")
    if entry is not None:
        st.info(entry.describe())
        for w in entry.warnings:
            st.warning(w)
        col1, col2 = st.columns(2)
        if col1.button("Kaydet", type="primary", key="quick_save"):
            st.session_state.pop("_quick_pending", None)
            run_action(lambda: c.quick.save(entry), "İşlem kaydedildi")
        if col2.button("Vazgeç", key="quick_cancel"):
            st.session_state.pop("_quick_pending", None)
            st.rerun()


def broker_import(c) -> None:  # type: ignore[no-untyped-def]
    st.markdown("#### Aracı kurum / banka dökümü")
    st.caption(
        "Aracı kurum, portföy uygulaması ya da banka dökümünü (CSV ya da .xlsx) yükle. Kolonlar başlıklardan "
        "otomatik eşlenir; gerekirse düzelt ve eşlemeyi profil olarak kaydet, sonraki dökümlerde tek seçimle gelsin. "
        "Fiyat kolonu yoksa tutar ÷ adet kullanılır. Temettü, virman gibi alış/satış olmayan satırlar atlanır; "
        "aynı işlem iki kez eklenmez."
    )
    upload = st.file_uploader("Döküm dosyası", type=["csv", "txt", "xlsx"], key="broker_up")
    if upload is None:
        return
    data = upload.getvalue()
    try:
        base = c.broker_import.preview(data)
    except DivitrackError as exc:
        st.error(str(exc))
        return
    profiles = c.broker_import.profiles()
    names = ["Otomatik"] + sorted(profiles)
    choice = st.selectbox(
        "Kolon eşlemesi", names, help="Kaydettiğin profiller: " + (", ".join(sorted(profiles)) or "yok")
    )
    cols = base.columns if choice == "Otomatik" else c.broker_import.apply_profile(profiles[choice], base.header)
    options = [None, *range(len(base.header))]
    grid = st.columns(4)
    chosen: dict[str, int | None] = {}
    for i, f in enumerate(FIELDS):
        current = cols.get(f)
        chosen[f] = grid[i % 4].selectbox(
            FIELD_LABELS[f],
            options,
            index=options.index(current) if current in options else 0,
            format_func=lambda o: "—" if o is None else base.header[o],
            key=f"bcol_{f}",
        )
    pv = c.broker_import.preview(data, chosen)
    p = pv.parsed
    st.caption(f"{len(p.rows)} işlem okundu · {p.skipped} satır atlandı (alış/satış değil) · {len(p.errors)} hata")
    for e in p.errors[:10]:
        st.warning(e)
    if p.rows:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Satır": r.line,
                        "Tarih": r.trade_date,
                        "Sembol": r.symbol,
                        "İşlem": "Alış" if r.tx_type == "BUY" else "Satış",
                        "Adet": float(r.quantity),
                        "Fiyat": float(r.price),
                        "Komisyon": float(r.fee),
                        "Para birimi": r.currency.value if r.currency else "",
                    }
                    for r in p.rows
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={"Tarih": st.column_config.DateColumn(format="DD.MM.YYYY")},
        )
    new_assets: dict[str, tuple[AssetClass, Currency]] = {}
    if pv.unknown_symbols:
        st.markdown("**Kayıtlı olmayan semboller** — sınıfını seç ya da *Atla* bırak:")
        classes: list[AssetClass | None] = [None, *AssetClass]
        grid = st.columns(3)
        for i, sym in enumerate(pv.unknown_symbols):
            cls = grid[i % 3].selectbox(
                sym, classes, format_func=lambda x: "Atla" if x is None else x.label, key=f"bnew_{sym}"
            )
            if cls is not None:
                ccy = Currency.USD if cls is AssetClass.US_STOCK else Currency.TRY
                new_assets[sym] = (cls, ccy)
    col1, col2, col3 = st.columns([1, 1, 2])
    profile_name = col3.text_input("Eşlemeyi profil olarak kaydet", placeholder=" / ".join(PRESET_NAMES), key="bprof")
    if col2.button("Profili kaydet", disabled=not profile_name.strip()):
        run_action(
            lambda: c.broker_import.save_profile(profile_name, pv.header, chosen), f"'{profile_name}' kaydedildi"
        )
    if col1.button("İçe aktar", type="primary", disabled=not p.rows):
        run_action(lambda: flash(str(c.broker_import.import_rows(p.rows, new_assets))))


# =========================================================================== Ayarlar
def expectations(c) -> None:  # type: ignore[no-untyped-def]
    st.subheader("Beklentiler ve vergi")
    expected = c.plans.expected_cpi()
    exemption = c.tax.exemption()
    with st.form("expectations"):
        col1, col2 = st.columns(2)
        e = col1.text_input(
            "Önümüzdeki 12 ay beklenen enflasyon (%)",
            value=fmt_qty(expected) if expected is not None else "",
            help="TCMB Piyasa Katılımcıları Anketi'ndeki 12 ay sonrası TÜFE beklentisini girebilirsin. "
            "Başa baş tablosunda '12 ay sonra' kolonunu hesaplar.",
        )
        x = col2.text_input(
            "Yurt dışı hisse kazancı istisnası (TL, isteğe bağlı)",
            value=fmt_qty(exemption) if exemption else "",
            help="Vergi ön izlemesinde net kazançtan düşülür. Güncel tutarı ve uygulanıp uygulanmadığını "
            "mali müşavirine sor.",
        )
        if st.form_submit_button("Kaydet"):

            def save() -> None:
                c.plans.set_expected_cpi(_dec(e, "Beklenen enflasyon"))
                c.tax.set_exemption(_dec(x, "İstisna"))

            run_action(save, "Kaydedildi")


def ppi(c) -> None:  # type: ignore[no-untyped-def]
    st.subheader("Yİ-ÜFE (vergi endekslemesi için)")
    st.caption(
        "Aylık Yİ-ÜFE endeksi (2003=100). TÜİK veya TCMB EVDS'den alabilirsin; yalnızca satış kazancı "
        "ön izlemesinde kullanılır."
    )
    index = c.ppi.all()
    with st.form("ppi_add", clear_on_submit=True):
        col1, col2 = st.columns(2)
        month = col1.text_input("Ay (YYYY-AA)", placeholder="2026-10")
        value = col2.text_input("Endeks", placeholder="4.123,45")
        if st.form_submit_button("Ekle / güncelle"):
            run_action(lambda: c.ppi.upsert(month.strip(), to_decimal(value, field="Endeks")), f"{month} kaydedildi")
    if index:
        st.dataframe(
            pd.DataFrame(sorted(index.items(), reverse=True), columns=["Ay", "Endeks"]).astype({"Endeks": float}),
            hide_index=True,
            height=200,
        )


def alerts(c) -> None:  # type: ignore[no-untyped-def]
    st.subheader("Alarmlar")
    st.caption(
        "Portföy Özeti her açıldığında ve `python -m divitrack.cli check` komutu çalıştığında denetlenir. "
        "Telegram ayarlıysa tetiklenen alarm günde bir kez mesaj olarak gelir."
    )
    assets = c.portfolio.assets.list()
    with st.form("alert_add", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        kind = col1.selectbox("Tür", list(AlertKind), format_func=lambda k: k.label)
        asset = col2.selectbox(
            "Varlık (portföy düşüşünde gerekmez)", [None, *assets], format_func=lambda a: "—" if a is None else a.symbol
        )
        threshold = col3.text_input("Eşik (fiyat TL/birim ya da %)")
        note = st.text_input("Not (isteğe bağlı)")
        if st.form_submit_button("Alarm ekle"):
            run_action(
                lambda: c.alerts.add(
                    Alert(kind, to_decimal(threshold, field="Eşik"), asset.id if asset is not None else None, note)
                ),
                "Alarm eklendi",
            )
    states = c.alerts.evaluate()
    for s in states:
        col1, col2, col3 = st.columns([6, 1, 1])
        icon = "🔴" if s.triggered else ("⚪" if s.alert.active else "⏸️")
        col1.markdown(f"{icon} {s.message.removeprefix('🔔 ')}")
        if col2.button("Durdur" if s.alert.active else "Aç", key=f"al_t_{s.alert.id}"):
            run_action(lambda s=s: c.alerts.set_active(s.alert, not s.alert.active))
        if col3.button("Sil", key=f"al_d_{s.alert.id}"):
            run_action(lambda s=s: c.alerts.delete(s.alert.id or -1), "Alarm silindi")


def notifications(c) -> None:  # type: ignore[no-untyped-def]
    st.subheader("Bildirimler (Telegram)")
    n = c.notifier
    st.caption(
        "1) Telegram'da @BotFather ile bir bot oluştur ve anahtarı (token) kopyala. 2) Botuna bir mesaj at; "
        "sohbet kimliğini @userinfobot'tan öğren. 3) Aşağıya gir. Anahtar yedeğe yazılmaz. Bot ile mesajdan "
        "işlem girmek için: `python -m divitrack.cli bot`."
    )
    with st.form("telegram"):
        col1, col2 = st.columns(2)
        token = col1.text_input("Bot anahtarı", type="password", placeholder="kayıtlı" if n.token else "123456:ABC…")
        chat = col2.text_input("Sohbet kimliği (chat id)", value=n.chat_id)
        if st.form_submit_button("Kaydet"):
            run_action(lambda: n.configure(token or None, chat), "Telegram ayarları kaydedildi")
    if n.configured and st.button("Deneme mesajı gönder"):
        if n.send(f"DiviTrack bağlantı denemesi ✅ {datetime.now():%d.%m.%Y %H:%M}"):
            st.success("Mesaj gönderildi")
        else:
            st.error("Gönderilemedi: anahtarı, sohbet kimliğini ve internet bağlantısını kontrol et.")


def security(c) -> None:  # type: ignore[no-untyped-def]
    st.subheader("Güvenlik")
    s = c.security
    st.caption(
        "Parola uygulamayı açılışta kilitler (PBKDF2 ile özetlenir, kendisi saklanmaz). Veritabanı dosyası diskte "
        "şifresiz durur; tam koruma için disk şifrelemesi (BitLocker/FileVault) kullan. Şifreli yedek, başka bir "
        "yerde saklayacağın kopyalar içindir."
    )
    with st.form("password"):
        col1, col2 = st.columns(2)
        current = col1.text_input("Mevcut parola", type="password", disabled=not s.has_password)
        new = col2.text_input("Yeni parola (en az 8 karakter)", type="password")
        a, b = st.columns(2)
        if a.form_submit_button("Parolayı kaydet", type="primary"):
            run_action(lambda: s.set_password(new, current or None), "Parola kaydedildi")
        if s.has_password and b.form_submit_button("Parolayı kaldır"):
            run_action(lambda: s.clear_password(current), "Parola kaldırıldı")
    st.markdown("**Şifreli yedek**")
    col1, col2 = st.columns(2)
    with col1, st.form("enc_backup"):
        pw = st.text_input("Yedek parolası", type="password")
        if st.form_submit_button("Şifreli yedek oluştur"):
            try:
                st.session_state["enc_blob"] = s.encrypt(c.transfer.backup_db(), pw)
            except DivitrackError as exc:
                st.error(str(exc))
    blob = st.session_state.get("enc_blob")
    if blob:
        col1.download_button(
            "İndir (.dtenc)", blob, f"divitrack-{datetime.now():%Y%m%d-%H%M}.dtenc", "application/octet-stream"
        )
    with col2:
        up = st.file_uploader("Şifreli yedeği çöz", type=["dtenc"], key="enc_up")
        pw2 = st.text_input("Parola", type="password", key="enc_pw2")
        if up is not None and pw2 and st.button("Çöz"):
            try:
                st.session_state["dec_blob"] = s.decrypt(up.getvalue(), pw2)
            except DivitrackError as exc:
                st.error(str(exc))
        dec = st.session_state.get("dec_blob")
        if dec:
            st.download_button("Çözülmüş veritabanı (.db)", dec, "divitrack-geri.db", "application/x-sqlite3")
            st.caption("Geri yüklemek için uygulamayı kapat ve bu dosyayı veritabanı konumuna koy.")
