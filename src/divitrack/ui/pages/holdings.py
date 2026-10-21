"""Varlıklar & İşlemler: varlık tanımlama, alım/satım kaydı, işlem geçmişi."""

from __future__ import annotations

from datetime import date

import streamlit as st

from divitrack.core.entities import Asset, Transaction
from divitrack.core.enums import AssetClass, Currency, Purpose, TxType
from divitrack.core.money import to_decimal
from divitrack.services.market.symbols import METAL_PRESETS
from divitrack.ui.components.widgets import sidebar
from divitrack.ui.formatting import fmt_qty
from divitrack.ui.state import get_container, run_action, show_flashes
from divitrack.ui.tables import transactions_frame

DEFAULT_CCY = {
    AssetClass.BIST_STOCK: Currency.TRY,
    AssetClass.US_STOCK: Currency.USD,
    AssetClass.PRECIOUS_METAL: Currency.TRY,
    AssetClass.FUND: Currency.TRY,
    AssetClass.CASH: Currency.TRY,
}
DEFAULT_PURPOSE = {
    AssetClass.PRECIOUS_METAL: Purpose.DEFENSIVE,
    AssetClass.CASH: Purpose.DEFENSIVE,
}
PURPOSES = list(Purpose)
TX_HELP = (
    "**Bedelsiz / bölünme:** yeni gelen adedi girin, fiyat 0 sayılır (ortalama maliyet düşer). "
    "**Bedelli (rüçhan):** kullanılan adet ve ödenen birim fiyat. "
    "**Temettüden alış:** temettüyle alınan adet; yeni para sayılmaz. Fiyatı 0 girerseniz maliyetsiz eklenir."
)
SYMBOL_HELP = {
    AssetClass.BIST_STOCK: "Örn. THYAO, TUPRS (.IS soneki otomatik eklenir)",
    AssetClass.US_STOCK: "Örn. AAPL, SCHD, VOO",
    AssetClass.PRECIOUS_METAL: "Hazır sembollerden birini seçin veya Yahoo sembolü girin (ör. GC=F)",
    AssetClass.FUND: "TEFAS kodu (ör. TTE). Fiyat Yahoo'da yoksa manuel birim fiyat girin",
    AssetClass.CASH: "Serbest ad: NAKIT-TRY, BES-FON1… (fiyat çekilmez)",
}


def _asset_form() -> None:
    c = get_container()
    st.markdown("#### Yeni varlık")
    asset_class = st.selectbox("Varlık sınıfı", list(AssetClass), format_func=lambda a: a.label)
    with st.form("asset_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        if asset_class is AssetClass.PRECIOUS_METAL:
            preset = col1.selectbox(
                "Maden",
                [*METAL_PRESETS, "Diğer"],
                format_func=lambda s: METAL_PRESETS[s][0] if s in METAL_PRESETS else s,
            )
            custom = col1.text_input("Yahoo sembolü (Diğer seçildiyse)")
            symbol = custom if preset == "Diğer" else preset
            default_ccy = METAL_PRESETS[preset][1] if preset in METAL_PRESETS else Currency.USD
        else:
            symbol = col1.text_input("Sembol", help=SYMBOL_HELP[asset_class])
            default_ccy = DEFAULT_CCY[asset_class]
        currencies = list(Currency)
        currency = col2.selectbox(
            "Para birimi",
            currencies,
            index=currencies.index(default_ccy),
            disabled=asset_class is AssetClass.BIST_STOCK,
        )
        name = col1.text_input("Ad (opsiyonel)")
        purpose = col1.selectbox(
            "Amaç",
            PURPOSES,
            index=PURPOSES.index(DEFAULT_PURPOSE.get(asset_class, Purpose.UNASSIGNED)),
            format_func=lambda p: p.label,
            help="Dağılım hedefleri ve aylık plan amaca göre hesaplanır. "
            + " · ".join(f"{p.label}: {p.hint}" for p in Purpose.targeted()),
        )
        manual = col2.text_input(
            "Manuel birim fiyat (opsiyonel)",
            help="Fiyatı API'den gelmeyen varlıklar (BES fonu vb.) için. Nakit için boş bırakın: "
            "adet = tutar kabul edilir.",
        )
        if st.form_submit_button("Varlık ekle", type="primary"):

            def add() -> Asset:
                return c.portfolio.add_asset(
                    Asset(
                        symbol=symbol,
                        asset_class=asset_class,
                        currency=Currency.TRY if asset_class is AssetClass.BIST_STOCK else currency,
                        name=name,
                        manual_price=to_decimal(manual, field="Manuel fiyat") if manual.strip() else None,
                        purpose=purpose,
                    )
                )

            run_action(add, f"{symbol.upper()} eklendi")


def _transaction_form(assets: list[Asset]) -> None:
    c = get_container()
    st.markdown("#### İşlem kaydı")
    st.caption(TX_HELP)
    if not assets:
        st.info("Önce bir varlık ekleyin.")
        return
    with st.form("tx_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        asset = col1.selectbox("Varlık", assets, format_func=lambda a: f"{a.display_name} ({a.currency.value})")
        tx_type = col2.selectbox("İşlem", list(TxType), format_func=lambda t: t.label)
        trade_date = col3.date_input("Tarih", value=date.today(), max_value=date.today(), format="DD.MM.YYYY")
        quantity = col1.text_input("Adet / Tutar", placeholder="10 veya 1.250,50")
        price = col2.text_input("Birim fiyat", placeholder="Nakit için 1", help="Varlığın kendi para biriminde")
        fee = col3.text_input("Komisyon", value="0")
        note = st.text_input("Not (opsiyonel)")
        if st.form_submit_button("Kaydet", type="primary"):

            def add() -> Transaction:
                assert asset.id is not None
                return c.portfolio.add_transaction(
                    Transaction(
                        asset_id=asset.id,
                        tx_type=tx_type,
                        trade_date=trade_date,
                        quantity=to_decimal(quantity, field="Adet"),
                        price=to_decimal(price or _default_price(asset, tx_type), field="Fiyat"),
                        fee=to_decimal(fee or "0", field="Komisyon"),
                        note=note,
                    )
                )

            run_action(add, "İşlem kaydedildi")


def _default_price(asset: Asset, tx_type: TxType) -> str:
    if tx_type is TxType.BONUS:
        return "0"
    return "1" if asset.asset_class is AssetClass.CASH else ""


def _asset_manager(assets: list[Asset]) -> None:
    c = get_container()
    positions = {a.id: s for a, s in c.portfolio.positions()}
    for a in assets:
        state = positions.get(a.id)
        qty = fmt_qty(state.quantity) if state else "0"
        label = f"{a.display_name} · {a.asset_class.label} · {a.purpose.label} · {a.currency.value} · adet {qty}"
        with st.expander(label):
            _asset_editor(a)


def _asset_editor(asset: Asset) -> None:
    c = get_container()
    with st.form(f"edit_{asset.id}"):
        name = st.text_input("Ad", value=asset.name)
        purpose = st.selectbox(
            "Amaç", PURPOSES, index=PURPOSES.index(asset.purpose), format_func=lambda p: p.label, key=f"pp_{asset.id}"
        )
        manual = st.text_input(
            "Manuel fiyat (boş = API)", value="" if asset.manual_price is None else str(asset.manual_price)
        )
        col1, col2 = st.columns(2)
        update = col1.form_submit_button("Güncelle")
        confirm = col2.checkbox("Silmeyi onayla (işlemler ve temettüler de silinir)", key=f"cf_{asset.id}")
        delete = col2.form_submit_button("Sil")
    if update:

        def upd() -> Asset:
            asset.name = name
            asset.purpose = purpose
            asset.manual_price = to_decimal(manual, field="Manuel fiyat") if manual.strip() else None
            return c.portfolio.update_asset(asset)

        run_action(upd, f"{asset.symbol} güncellendi")
    if delete:
        if confirm:
            run_action(lambda: c.portfolio.delete_asset(asset.id or -1), f"{asset.symbol} silindi")
        else:
            st.warning("Silmek için onay kutusunu işaretleyin.")


def render() -> None:
    c = get_container()
    sidebar(c)
    show_flashes()
    st.title("Varlıklar & İşlemler")

    assets = c.portfolio.assets.list()
    tab_tx, tab_assets, tab_history = st.tabs(["Yeni İşlem", "Varlıklar", "İşlem Geçmişi"])
    with tab_tx:
        _transaction_form(assets)
    with tab_assets:
        _asset_form()
        if assets:
            st.markdown("#### Kayıtlı varlıklar")
            _asset_manager(assets)
    with tab_history:
        txs = c.portfolio.transactions.list()
        if not txs:
            st.info("Henüz işlem yok.")
            return
        by_id = {a.id: a for a in assets if a.id is not None}
        df = transactions_frame(txs, by_id)
        symbols = ["Tümü", *sorted({a.symbol for a in assets})]
        pick = st.selectbox("Filtre", symbols)
        if pick != "Tümü":
            df = df[df["Sembol"] == pick]
        st.dataframe(
            df,
            hide_index=True,
            width="stretch",
            column_config={"Tarih": st.column_config.DateColumn(format="DD.MM.YYYY")},
        )
        with st.form("del_tx"):
            tx_id = st.selectbox("Silinecek işlem", df["ID"].tolist(), format_func=lambda i: _tx_label(df, i))
            if st.form_submit_button("İşlemi sil"):
                run_action(lambda: c.portfolio.delete_transaction(int(tx_id)), "İşlem silindi")


def _tx_label(df: object, tx_id: int) -> str:
    import pandas as pd

    assert isinstance(df, pd.DataFrame)
    row = df[df["ID"] == tx_id].iloc[0]
    return f"#{tx_id} · {row['Tarih']:%d.%m.%Y} · {row['Sembol']} · {row['İşlem']} {row['Adet']:g} @ {row['Fiyat']:g}"
