"""Ayarlar & Veri: hedefler ve kurallar, TÜFE, stopaj oranları, dışa/içe aktarma, yedekleme."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import streamlit as st

from divitrack.core.enums import AssetClass, Purpose
from divitrack.core.exceptions import ValidationError
from divitrack.core.inflation import index_from_changes, latest_month, parse_month
from divitrack.core.money import HUNDRED, to_decimal
from divitrack.services.budget_service import add_months
from divitrack.services.insights_service import PlanRules
from divitrack.services.io.transfer import TX_COLUMNS
from divitrack.ui.components.widgets import sidebar
from divitrack.ui.state import flash, get_container, run_action, show_flashes
from divitrack.ui.tables import cpi_frame

CSV_EXAMPLE = (
    "date,symbol,asset_class,currency,type,quantity,price,fee,note\n"
    "2024-01-15,THYAO,BIST_STOCK,TRY,BUY,100,285.50,12.5,\n"
    "2024-02-01,SCHD,US_STOCK,USD,BUY,10,76.20,0,\n"
    "2024-03-10,XAU-GRAM,PRECIOUS_METAL,TRY,BUY,5,2150,0,Gram altın\n"
    "2024-03-10,NAKIT-USD,CASH,USD,BUY,1000,1,0,Döviz hesabı\n"
)


def render() -> None:
    c = get_container()
    sidebar(c)
    show_flashes()
    st.title("Ayarlar & Veri")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")

    _rules()
    _cpi()

    # --- Stopaj ------------------------------------------------------------------
    st.subheader("Temettü stopaj oranları")
    st.caption(
        "Varsayılanlar: BIST %15, ABD %20 (TR–ABD çifte vergilendirme anlaşması, W-8BEN ile). "
        "Mevzuat değişebilir; kendi durumunuza göre güncelleyin. Vergi tavsiyesi değildir."
    )
    with st.form("withholding"):
        cols = st.columns(2)
        values = {}
        for col, ac in zip(cols, (AssetClass.BIST_STOCK, AssetClass.US_STOCK), strict=True):
            current = c.dividends.withholding_rate(ac) * 100
            values[ac] = col.text_input(f"{ac.label} (%)", value=format(current.normalize(), "f"))
        if st.form_submit_button("Kaydet"):

            def save() -> None:
                for ac, raw in values.items():
                    c.dividends.set_withholding_rate(ac, to_decimal(raw, field=ac.label) / 100)

            run_action(save, "Stopaj oranları kaydedildi")

    # --- Dışa aktarma ----------------------------------------------------------
    st.subheader("Dışa aktar")
    col1, col2, col3, col4 = st.columns(4)
    col1.download_button("Tam yedek (JSON)", c.transfer.export_json(), f"divitrack-{stamp}.json", "application/json")
    col2.download_button(
        "İşlemler (CSV)", c.transfer.export_transactions_csv(), f"divitrack-islemler-{stamp}.csv", "text/csv"
    )
    col3.download_button(
        "Temettüler (CSV)", c.transfer.export_dividends_csv(), f"divitrack-temettu-{stamp}.csv", "text/csv"
    )
    col4.download_button("Veritabanı (.db)", c.transfer.backup_db(), f"divitrack-{stamp}.db", "application/x-sqlite3")

    # --- İçe aktarma ------------------------------------------------------------
    st.subheader("İçe aktar")
    tab_json, tab_csv = st.tabs(["JSON yedeği", "İşlem CSV"])
    with tab_json:
        upload = st.file_uploader("DiviTrack JSON dosyası", type=["json"], key="json_up")
        replace = st.checkbox(
            "Mevcut verileri sil ve yedekten geri yükle",
            help="İşaretli değilse veriler birleştirilir; aynı kayıtlar tekrar eklenmez.",
        )
        if upload is not None and st.button("JSON'u içe aktar", type="primary"):
            run_action(lambda: flash(str(c.transfer.import_json(upload.getvalue(), replace=replace))))
    with tab_csv:
        st.caption(
            f"Kolonlar: `{','.join(TX_COLUMNS)}` — ayraç virgül veya noktalı virgül olabilir. "
            "Tarih YYYY-AA-GG. Varlıklar yoksa otomatik oluşturulur. Hatalı bir satır varsa hiçbir "
            "şey yazılmaz."
        )
        st.download_button("Örnek CSV indir", CSV_EXAMPLE, "divitrack-ornek.csv", "text/csv")
        upload_csv = st.file_uploader("İşlem CSV dosyası", type=["csv", "txt"], key="csv_up")
        if upload_csv is not None and st.button("CSV'yi içe aktar", type="primary"):

            def do_import() -> None:
                report = c.transfer.import_transactions_csv(upload_csv.getvalue())
                flash(str(report))

            run_action(do_import)

    # --- Bakım -------------------------------------------------------------------
    st.subheader("Bakım")
    col1, col2 = st.columns(2)
    if col1.button("Fiyat önbelleğini temizle"):
        run_action(c.market.clear_cache, "Önbellek temizlendi")
    if col2.button("Değer geçmişini sıfırla"):
        run_action(lambda: c.snapshots.snapshots.clear(), "Değer geçmişi silindi")
    st.caption(f"Veritabanı konumu: `{c.settings.db_path}`")


def _pct_text(value: Decimal) -> str:
    return format((value * HUNDRED).normalize(), "f")


def _rules() -> None:
    c = get_container()
    st.subheader("Hedefler ve kurallar")
    st.caption(
        "Amaç hedeflerinin toplamı %100 olmalı. Deneme hedefi aynı zamanda deneme bütçesidir. "
        "Bu değerler sağlık puanını, uyarıları ve aylık planı belirler."
    )
    rules = c.insights.rules()
    with st.form("rules"):
        cols = st.columns(len(Purpose.targeted()))
        targets = {
            p: col.text_input(f"{p.label} (%)", value=_pct_text(rules.targets.get(p, Decimal(0))), help=p.hint)
            for col, p in zip(cols, Purpose.targeted(), strict=True)
        }
        col1, col2, col3 = st.columns(3)
        single = col1.text_input(
            "Tek kalem sınırı (%)",
            value=_pct_text(rules.single_position_max),
            help="Bir varlığın portföydeki en yüksek payı",
        )
        tol = col2.text_input(
            "Sapma toleransı (puan)",
            value=_pct_text(rules.drift_tolerance),
            help="Amaç payı hedeften bu kadar saparsa uyarı verilir",
        )
        limit = col3.text_input(
            f"{date.today().year} yurt dışı temettü beyan sınırı (TL)",
            value="" if rules.declaration_limit is None else format(rules.declaration_limit.normalize(), "f"),
            help="Gelir İdaresi'nin ilgili yıl için açıkladığı tutar; her yıl güncelleyin",
        )
        col1, col2 = st.columns(2)
        savings = col1.text_input(
            "Hedef tasarruf oranı (%)",
            value=_pct_text(c.budget.savings_target),
            help="Aylık net tasarruf / gelir",
        )
        share = col2.text_input(
            "Tasarrufun yatırıma ayrılan kısmı (%)",
            value=_pct_text(c.budget.invest_share),
            help="Kalan kısım nakit/acil durum fonunda bırakılır",
        )
        if st.form_submit_button("Kuralları kaydet", type="primary"):

            def save() -> None:
                new = PlanRules(
                    targets={p: to_decimal(v, field=p.label) / HUNDRED for p, v in targets.items()},
                    single_position_max=to_decimal(single, field="Tek kalem sınırı") / HUNDRED,
                    drift_tolerance=to_decimal(tol, field="Sapma toleransı") / HUNDRED,
                    declaration_limit=to_decimal(limit, field="Beyan sınırı") if limit.strip() else None,
                )
                c.insights.save_rules(new)
                c.budget.set_savings_target(to_decimal(savings, field="Tasarruf oranı") / HUNDRED)
                c.budget.set_invest_share(to_decimal(share, field="Yatırım payı") / HUNDRED)

            run_action(save, "Kurallar kaydedildi")


def _cpi() -> None:
    c = get_container()
    st.subheader("TÜFE (enflasyon endeksi)")
    index = c.cpi.all()
    last = latest_month(index)
    st.caption(
        "Reel getiri için aylık TÜFE endeksi gerekir. TÜİK'in açıkladığı endeks değerini (2003=100) ya da "
        "aylık % değişimi girin; uygulama internetten veri çekmez. "
        + (f"Son girilen ay: **{last}**." if last else "Henüz veri yok.")
    )
    tab_add, tab_table = st.tabs(["Ay ekle", "Tablo"])
    with tab_add, st.form("cpi_add", clear_on_submit=True):
        next_month = f"{date.today().year}-01"
        if last:
            y, m = (int(x) for x in last.split("-"))
            nxt = add_months(date(y, m, 1), 1)
            next_month = f"{nxt.year}-{nxt.month:02d}"
        col1, col2, col3 = st.columns(3)
        month = col1.text_input("Ay (YYYY-AA)", value=next_month)
        mode = col2.radio("Girilen değer", ["Endeks", "Aylık % değişim"], horizontal=True)
        value = col3.text_input("Değer", placeholder="ör. 3412,58 veya 2,05")
        if st.form_submit_button("Ekle"):

            def add() -> None:
                key = parse_month(month)
                number = to_decimal(value, field="Değer")
                if mode == "Endeks":
                    c.cpi.upsert(key, number)
                    return
                if not index:
                    # İlk veri: bir önceki ayı 100 kabul ederek zinciri başlat
                    y, m = (int(x) for x in key.split("-"))
                    prev = add_months(date(y, m, 1), -1)
                    c.cpi.replace_all(index_from_changes(f"{prev.year}-{prev.month:02d}", [(key, number)]))
                    return
                prev_key = max((k for k in index if k < key), default=None)
                if prev_key is None:
                    raise ValidationError("Aylık % değişim için önceki ayın endeksi olmalı")
                c.cpi.upsert(key, index[prev_key] * (1 + number / HUNDRED))

            run_action(add, "TÜFE kaydedildi")
    with tab_table:
        if not index:
            st.info("Henüz TÜFE verisi yok.")
        else:
            edited = st.data_editor(
                cpi_frame(index)[["Ay", "Endeks"]],
                hide_index=True,
                num_rows="dynamic",
                width="stretch",
                column_config={"Endeks": st.column_config.NumberColumn(format="%.4f", min_value=0)},
                key="cpi_editor",
            )
            st.caption("Satır ekleyip silebilir, değerleri düzeltebilirsiniz.")
            if st.button("Tabloyu kaydet"):

                def save() -> None:
                    new: dict[str, Decimal] = {}
                    for row in edited.itertuples(index=False):
                        if not row[0] or row[1] is None or pd.isna(row[1]):
                            continue
                        new[parse_month(str(row[0]))] = Decimal(str(row[1]))
                    c.cpi.replace_all(new)

                run_action(save, "TÜFE tablosu kaydedildi")
