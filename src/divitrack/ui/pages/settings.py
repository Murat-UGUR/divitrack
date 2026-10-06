"""Ayarlar & Veri: stopaj oranları, dışa/içe aktarma, yedekleme."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from divitrack.core.enums import AssetClass
from divitrack.core.money import to_decimal
from divitrack.services.io.transfer import TX_COLUMNS
from divitrack.ui.components.widgets import sidebar
from divitrack.ui.state import flash, get_container, run_action, show_flashes

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
