"""Aylık Plan: bütçeden kalan parayı amaçlara bölme ve aylık rutin."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import streamlit as st

from divitrack.core.entities import Goal
from divitrack.core.enums import Currency, Purpose
from divitrack.core.exceptions import DivitrackError
from divitrack.core.money import HUNDRED, ZERO, to_decimal
from divitrack.services.budget_service import add_months, month_start
from divitrack.ui.components.widgets import sidebar
from divitrack.ui.formatting import fmt_money
from divitrack.ui.pages.budget import month_label
from divitrack.ui.state import get_container, run_action, show_flashes
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

    _goals()

    st.subheader("Aylık rutin")
    st.caption("Her ay aynı sırayla: önce veriyi güncelle, sonra karar ver. İşaretler yalnızca bu oturumda tutulur.")
    for i, (title, detail) in enumerate(ROUTINE, start=1):
        st.checkbox(f"**{i}. {title}** — {detail}", key=f"routine_{i}")


STATUS_ICON = {"Ulaşıldı": "✅", "Yolunda": "🟢", "Geride": "🟠", "Süre doldu": "🔴", "Tasarruf verisi yok": "⚪"}


def _goals() -> None:
    c = get_container()
    st.subheader("Birikim hedefleri")
    st.caption(
        "Ev peşinatı, acil durum fonu, araba… Hedefe bağladığınız varlıkların güncel TL değeri ve elle "
        "girdiğiniz ek birikim ilerleme sayılır. Hız, son 3 tamamlanmış ayın yatırılabilir tasarruf ortalamasıdır "
        "(Bütçe); birden fazla hedef aynı hızı paylaşır."
    )
    progress = c.goals.progress()
    pace = c.goals.monthly_pace()
    if progress:
        total_required = sum((gp.required_monthly or ZERO for gp in progress if gp.months_left > 0), ZERO)
        col1, col2 = st.columns(2)
        col1.metric("Tüm hedefler için aylık gereken", fmt_money(total_required, Currency.TRY))
        col2.metric(
            "Son aylardaki ortalama hız",
            fmt_money(pace, Currency.TRY),
            delta=None if pace is None else ("Yetiyor" if pace >= total_required else "Yetmiyor"),
            delta_color="normal" if pace is None or pace >= total_required else "inverse",
        )
        for gp in progress:
            with st.container(border=True):
                top, right = st.columns([3, 1])
                top.markdown(f"**{gp.goal.name}** · {gp.goal.target_date:%m.%Y} · {STATUS_ICON[gp.status]} {gp.status}")
                top.progress(float(gp.pct) / 100)
                right.markdown(
                    f"{fmt_money(gp.saved, Currency.TRY, 0)} / {fmt_money(gp.goal.target_amount, Currency.TRY, 0)}"
                )
                details = [f"{gp.months_left} ay kaldı"]
                if gp.required_monthly is not None:
                    details.append(f"ayda {fmt_money(gp.required_monthly, Currency.TRY, 0)} gerekiyor")
                if gp.months_needed:
                    details.append(f"bu hızla {gp.months_needed} ayda ulaşılır")
                if gp.symbols:
                    details.append("varlıklar: " + ", ".join(gp.symbols))
                top.caption(" · ".join(details))
    else:
        st.info("Henüz hedef yok.")

    assets = [a for a, s in c.portfolio.positions() if s.is_open]
    goals = c.goals.list()
    col1, col2 = st.columns(2)
    with col1, st.expander("Yeni hedef", expanded=not progress), st.form("goal_add", clear_on_submit=True):
        name = st.text_input("Ad", placeholder="Ev peşinatı")
        amount = st.text_input("Hedef tutar (TL)", placeholder="500.000")
        target = st.date_input("Hedef tarih", value=add_months(date.today(), 24), format="DD.MM.YYYY")
        linked = st.multiselect("Bağlı varlıklar", assets, format_func=lambda a: a.display_name)
        extra = st.text_input("Varlıklar dışında biriken (TL)", value="0", help="Ör. vadeli hesap, elde nakit")
        if st.form_submit_button("Hedef ekle", type="primary"):
            run_action(
                lambda: c.goals.add(
                    Goal(
                        name,
                        to_decimal(amount, field="Hedef tutar"),
                        target,
                        to_decimal(extra or "0", field="Ek birikim"),
                        tuple(a.id for a in linked if a.id is not None),
                    )
                ),
                "Hedef eklendi",
            )
    if goals:
        with col2, st.expander("Hedefi düzenle / sil"):
            goal = st.selectbox("Hedef", goals, format_func=lambda g: g.name, key="goal_pick")
            if goal is not None:
                with st.form(f"goal_edit_{goal.id}"):
                    amount = st.text_input("Hedef tutar (TL)", value=format(goal.target_amount.normalize(), "f"))
                    target = st.date_input("Hedef tarih", value=goal.target_date, format="DD.MM.YYYY")
                    linked = st.multiselect(
                        "Bağlı varlıklar",
                        assets,
                        default=[a for a in assets if a.id in goal.asset_ids],
                        format_func=lambda a: a.display_name,
                    )
                    extra = st.text_input("Ek birikim (TL)", value=format(goal.extra_saved.normalize(), "f"))
                    save = st.form_submit_button("Kaydet")
                    delete = st.form_submit_button("Hedefi sil")
                if save:
                    run_action(
                        lambda: c.goals.update(
                            Goal(
                                goal.name,
                                to_decimal(amount, field="Hedef tutar"),
                                target,
                                to_decimal(extra or "0", field="Ek birikim"),
                                tuple(a.id for a in linked if a.id is not None),
                                goal.id,
                            )
                        ),
                        "Hedef güncellendi",
                    )
                if delete:
                    run_action(lambda: c.goals.delete(goal.id or -1), "Hedef silindi")
