"""Aylık Plan: bütçeden kalan parayı amaçlara bölme ve aylık rutin."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import streamlit as st

from divitrack.core.enums import Currency, Purpose
from divitrack.core.exceptions import DivitrackError
from divitrack.core.money import HUNDRED, ZERO, to_decimal
from divitrack.services.budget_service import add_months, month_start
from divitrack.ui.components.widgets import sidebar
from divitrack.ui.formatting import fmt_money
from divitrack.ui.pages.budget import month_label
from divitrack.ui.state import get_container, show_flashes
from divitrack.ui.tables import plan_frame

NUM = st.column_config.NumberColumn

ROUTINE = [
    ("Gelir ve giderleri gir", "Bütçe sayfasında ayın kayıtlarını tamamla (CSV ile toplu da olur)."),
    ("TÜFE'yi güncelle", "TÜİK açıklayınca Ayarlar & Veri > TÜFE bölümüne geçen ayın endeksini ekle."),
    ("Fiyatları yenile", "Kenar çubuğundan fiyatları yenile; değer geçmişi otomatik kaydedilir."),
    ("Dikkat edilecekleri oku", "Portföy Özeti'ndeki uyarıları ve sağlık puanının en zayıf bileşenini incele."),
    ("Yatırımı böl ve uygula", "Bu sayfadaki öneriye göre alımları yap, işlemleri kaydet."),
    ("Kurumsal işlemleri işle", "Bedelsiz, bedelli ve temettüden alışları işlem defterine gir."),
    ("Temettüleri işaretle", "Ödenen temettüleri Ödendi yap; yurt dışı beyan sınırını kontrol et."),
    ("Yedek al", "Ayarlar & Veri > Tam yedek (JSON) indir."),
]


def render() -> None:
    c = get_container()
    base = sidebar(c)
    show_flashes()
    st.title("Aylık Plan")
    st.caption(
        "Bu ay yatıracağın parayı, satış yapmadan dağılımı hedefe en çok yaklaştıracak şekilde amaçlara böler: "
        "önce hedefin altında kalan amaçların açığı kapanır, kalan para hedef oranlarına göre dağılır."
    )

    this_month = month_start(date.today())
    source_month = st.selectbox(
        "Hangi ayın tasarrufu yatırılacak?",
        [this_month, add_months(this_month, -1)],
        format_func=month_label,
        key="plan_month",
    )
    budget_amount_try = c.budget.investable_amount(source_month)
    fx = c.market.fx_rates({Currency.TRY}, base).get(Currency.TRY)
    budget_amount = budget_amount_try * fx if fx is not None else ZERO

    col1, col2 = st.columns([2, 1])
    raw = col1.text_input(
        f"Yatırılacak tutar ({base.value})",
        value=format(budget_amount.quantize(Decimal("1")), "f") if budget_amount else "",
        placeholder="ör. 15.000",
        help="Varsayılan: seçilen ayın net tasarrufunun yatırıma ayrılan kısmı (Bütçe sayfası).",
        key=f"plan_amount_{source_month.isoformat()}_{base.value}",
    )
    col2.metric(f"Bütçeden ({month_label(source_month)})", fmt_money(budget_amount_try, Currency.TRY))
    if budget_amount_try == 0:
        st.caption("Bu ay için bütçede pozitif net tasarruf yok; tutarı elle girebilirsin.")

    try:
        amount = to_decimal(raw, field="Tutar") if raw.strip() else ZERO
    except DivitrackError as exc:
        st.error(str(exc))
        return

    summary = c.portfolio.summary(base)
    rules = c.insights.rules()
    if amount > 0:
        rows = c.insights.monthly_plan(amount, summary, rules)
        st.dataframe(
            plan_frame(rows),
            hide_index=True,
            width="stretch",
            column_config={
                "Yatırılacak": NUM(format="%.2f", help=base.value),
                "Şu anki pay %": NUM(format="%.1f"),
                "Sonraki pay %": NUM(format="%.1f"),
                "Hedef %": NUM(format="%.1f"),
            },
        )
        exp = next((r for r in rows if r.purpose is Purpose.EXPERIMENT), None)
        if exp and exp.amount > 0:
            st.caption(
                f"Deneme payı bütçesi %{rules.experiment_budget * HUNDRED:.0f}; deneme kalemlerine "
                f"en fazla {fmt_money(exp.amount, base)} ayır."
            )
        empty = [r.purpose.label for r in rows if r.amount > 0 and not r.symbols]
        if empty:
            st.info("Bu amaçlarda henüz varlık yok, yeni bir enstrüman seçmen gerekiyor: " + ", ".join(empty))
    else:
        st.info("Öneriyi görmek için yatırılacak tutarı gir.")

    st.subheader("Aylık rutin")
    st.caption("Her ay aynı sırayla: önce veriyi güncelle, sonra karar ver. İşaretler yalnızca bu oturumda tutulur.")
    for i, (title, detail) in enumerate(ROUTINE, start=1):
        st.checkbox(f"**{i}. {title}** — {detail}", key=f"routine_{i}")
