"""Bütçe: gelir/gider kayıtları, kategori bütçeleri, aylık trend ve yatırılabilir tutar."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import streamlit as st

from divitrack.core.entities import CashFlow, Category
from divitrack.core.enums import Currency, FlowKind
from divitrack.core.exceptions import DivitrackError
from divitrack.core.money import HUNDRED, to_decimal
from divitrack.core.statement import ColumnMap, parse_statement, read_table
from divitrack.services.budget_service import add_months, month_start
from divitrack.services.io.transfer import FLOW_COLUMNS
from divitrack.ui.components import charts
from divitrack.ui.components.widgets import sidebar
from divitrack.ui.formatting import fmt_money, fmt_pct, fmt_signed_money
from divitrack.ui.state import flash, get_container, run_action, show_flashes
from divitrack.ui.tables import budget_history_frame, budget_lines_frame, flows_frame

NUM = st.column_config.NumberColumn
TRY = Currency.TRY
MONTH_NAMES = [
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
]
CSV_EXAMPLE = (
    "date;category;amount;note\n"
    "2026-01-05;Maaş;65.000;Ocak maaşı\n"
    "2026-01-07;Market;-2.350,40;\n"
    "2026-01-10;Faturalar;-1.200;Elektrik + doğalgaz\n"
)


def month_label(d: date) -> str:
    return f"{MONTH_NAMES[d.month - 1]} {d.year}"


def render() -> None:
    c = get_container()
    sidebar(c)
    show_flashes()
    st.title("Bütçe")
    st.caption("Tutarlar TL'dir. Ay sonunda kalan net tasarruf, **Aylık Plan** sayfasında yatırıma bölünür.")

    this_month = month_start(date.today())
    months = [add_months(this_month, -i) for i in range(24)]
    month = st.selectbox("Ay", months, format_func=month_label, key="budget_month")
    summary = c.budget.month_summary(month)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Gelir", fmt_money(summary.income, TRY))
    k2.metric(
        "Gider",
        fmt_money(summary.expense, TRY),
        help=f"Toplam bütçe: {fmt_money(summary.budget_total, TRY)}" if summary.budget_total else None,
    )
    target = c.budget.savings_target * HUNDRED
    k3.metric(
        "Net tasarruf",
        fmt_signed_money(summary.net, TRY),
        delta=None if summary.savings_rate is None else f"{fmt_pct(summary.savings_rate, 1)} tasarruf oranı",
        delta_color="normal" if (summary.savings_rate or 0) >= target else "inverse",
        help=f"Hedef tasarruf oranı %{target:.0f} (Ayarlar & Veri)",
    )
    k4.metric(
        "Yatırıma ayrılabilir",
        fmt_money(c.budget.investable_amount(month), TRY),
        help=f"Net tasarrufun %{c.budget.invest_share * HUNDRED:.0f}'i (Ayarlar & Veri)",
    )
    for ln in summary.over_budget:
        st.warning(f"**{ln.category.name}** bütçesi {fmt_money(-(ln.remaining or 0), TRY)} aşıldı.")

    tab_add, tab_month, tab_trend, tab_stmt, tab_cats, tab_io = st.tabs(
        ["Kayıt ekle", "Bu ayın dökümü", "Aylık seyir", "Banka dökümü", "Kategoriler & kurallar", "İçe / dışa aktar"]
    )
    with tab_add:
        _add_form(month)
    with tab_month:
        _month_detail(summary, month)
    with tab_trend:
        _trend(month)
    with tab_stmt:
        _statement()
    with tab_cats:
        _categories()
        _rules()
    with tab_io:
        _import_export()


def _add_form(month: date) -> None:
    c = get_container()
    kind = st.radio("Tür", list(FlowKind), format_func=lambda k: k.label, horizontal=True, index=1, key="flow_kind")
    cats = c.budget.list_categories(kind)
    if not cats:
        st.info("Bu türde kategori yok; **Kategoriler & bütçe** sekmesinden ekleyin.")
        return
    default_day = date.today() if month == month_start(date.today()) else month
    with st.form("flow_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        cat = col1.selectbox("Kategori", cats, format_func=lambda x: x.name)
        amount = col2.text_input("Tutar (TL)", placeholder="1.250,50")
        flow_date = col3.date_input("Tarih", value=default_day, format="DD.MM.YYYY")
        note = st.text_input("Not (opsiyonel)")
        if st.form_submit_button("Kaydet", type="primary"):

            def add() -> CashFlow:
                assert cat.id is not None
                return c.budget.add_flow(CashFlow(cat.id, flow_date, abs(to_decimal(amount, field="Tutar")), note))

            run_action(add, f"{kind.label} kaydedildi")


def _month_detail(summary, month: date) -> None:  # type: ignore[no-untyped-def]
    c = get_container()
    lines = budget_lines_frame(summary)
    expenses = lines[lines["Tür"] == FlowKind.EXPENSE.label]
    if not expenses.empty:
        st.plotly_chart(
            charts.category_vs_budget(expenses, TRY.symbol), width="stretch", config={"displayModeBar": False}
        )
    if lines.empty:
        st.info(f"{month_label(month)} için kayıt yok.")
        return
    st.dataframe(
        lines,
        hide_index=True,
        width="stretch",
        column_config={
            "Gerçekleşen": NUM(format="%.2f"),
            "Bütçe": NUM(format="%.2f"),
            "Kalan": NUM(format="%.2f"),
            "Kullanım %": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100),
        },
    )
    flows = c.budget.flows_in_month(month)
    if not flows:
        return
    st.markdown("**Kayıtlar**")
    cats = {cat.id: cat for cat in c.budget.list_categories() if cat.id is not None}
    df = flows_frame(flows, cats)
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={"Tarih": st.column_config.DateColumn(format="DD.MM.YYYY"), "Tutar": NUM(format="%.2f")},
    )
    with st.form("del_flow"):
        pick = st.selectbox(
            "Silinecek kayıt",
            df["ID"].tolist(),
            format_func=lambda i: _flow_label(df, i),
        )
        if st.form_submit_button("Kaydı sil"):
            run_action(lambda: c.budget.delete_flow(int(pick)), "Kayıt silindi")


def _flow_label(df: pd.DataFrame, flow_id: int) -> str:
    row = df[df["ID"] == flow_id].iloc[0]
    return f"#{flow_id} · {row['Tarih']:%d.%m.%Y} · {row['Kategori']} · {fmt_money(row['Tutar'], TRY)}"


def _trend(month: date) -> None:
    c = get_container()
    history = c.budget.history(12, until=month)
    df = budget_history_frame(history)
    if not df[["Gelir", "Gider"]].to_numpy().any():
        st.info("Son 12 ayda kayıt yok.")
        return
    st.plotly_chart(charts.budget_trend(df, TRY.symbol), width="stretch", config={"displayModeBar": False})
    avg = c.budget.average_savings_rate(3, until=month)
    st.caption(f"Son 3 ayın tasarruf oranı: **{fmt_pct(avg, 1)}** · hedef %{c.budget.savings_target * HUNDRED:.0f}")
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={
            "Gelir": NUM(format="%.2f"),
            "Gider": NUM(format="%.2f"),
            "Net": NUM(format="%+.2f"),
            "Tasarruf %": NUM(format="%.1f"),
        },
    )


def _categories() -> None:
    c = get_container()
    expense = c.budget.list_categories(FlowKind.EXPENSE)
    st.markdown("**Gider kategorileri ve aylık bütçe**")
    st.caption("Bütçe boş bırakılırsa o kategori için sınır yok. Değiştirip **Bütçeleri kaydet**'e basın.")
    frame = pd.DataFrame(
        [
            {
                "ID": cat.id,
                "Kategori": cat.name,
                "Aylık bütçe (TL)": float(cat.monthly_budget) if cat.monthly_budget is not None else None,
            }
            for cat in expense
        ],
        columns=["ID", "Kategori", "Aylık bütçe (TL)"],
    )
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        disabled=["ID"],
        column_config={
            "ID": None,
            "Aylık bütçe (TL)": NUM(min_value=0, step=100, format="%.2f"),
        },
        key="budget_editor",
    )
    if st.button("Bütçeleri kaydet", type="primary"):

        def save() -> None:
            current = {cat.id: cat for cat in expense}
            for row in edited.itertuples(index=False):
                cat = current.get(int(row[0]))
                if cat is None:
                    continue
                name, raw = str(row[1]), row[2]
                amount = None if raw is None or pd.isna(raw) else Decimal(str(raw))
                if " ".join(name.split()) != cat.name:
                    c.budget.rename_category(int(row[0]), name)
                if amount != cat.monthly_budget:
                    c.budget.set_budget(int(row[0]), amount)

        run_action(save, "Bütçeler kaydedildi")

    income = c.budget.list_categories(FlowKind.INCOME)
    st.caption("Gelir kategorileri: " + (", ".join(cat.name for cat in income) or "—"))

    col1, col2 = st.columns(2)
    with col1, st.form("cat_add", clear_on_submit=True):
        st.markdown("**Yeni kategori**")
        name = st.text_input("Ad")
        kind = st.selectbox("Tür", list(FlowKind), index=1, format_func=lambda k: k.label)
        budget = st.text_input("Aylık bütçe (gider için, opsiyonel)")
        if st.form_submit_button("Ekle"):
            run_action(
                lambda: c.budget.add_category(
                    Category(name, kind, to_decimal(budget, field="Bütçe") if budget.strip() else None)
                ),
                "Kategori eklendi",
            )
    with col2, st.form("cat_del"):
        st.markdown("**Kategori sil**")
        all_cats = c.budget.list_categories()
        pick = st.selectbox("Kategori", all_cats, format_func=lambda x: f"{x.name} ({x.kind.label})")
        st.caption("Kaydı olan kategori silinemez.")
        if st.form_submit_button("Sil") and pick is not None:
            run_action(lambda: c.budget.delete_category(pick.id or -1), "Kategori silindi")


def _import_export() -> None:
    c = get_container()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    st.download_button(
        "Gelir/gider kayıtları (CSV)",
        c.transfer.export_cash_flows_csv(),
        f"divitrack-butce-{stamp}.csv",
        "text/csv",
    )
    st.caption(
        f"İçe aktarma kolonları: `{','.join(FLOW_COLUMNS)}` (kind ve note opsiyonel). Banka dökümündeki "
        "eksi tutarlar gider sayılır; olmayan kategoriler oluşturulur. Hatalı satır varsa hiçbir şey yazılmaz."
    )
    st.download_button("Örnek CSV indir", CSV_EXAMPLE, "divitrack-butce-ornek.csv", "text/csv")
    upload = st.file_uploader("Gelir/gider CSV dosyası", type=["csv", "txt"], key="flow_csv")
    if upload is not None and st.button("CSV'yi içe aktar", type="primary", key="flow_csv_go"):
        run_action(lambda: flash(str(c.transfer.import_cash_flows_csv(upload.getvalue()))))


# --------------------------------------------------------------------------- banka dökümü
GUESSES = {
    "date": ("tarih", "date", "işlem tarihi"),
    "description": ("açıklama", "aciklama", "description", "işlem", "detay"),
    "amount": ("tutar", "amount", "miktar"),
    "debit": ("borç", "borc", "debit", "çıkan"),
    "credit": ("alacak", "credit", "giren"),
}


def _guess(header: list[str], key: str, fallback: int | None) -> int | None:
    names = [h.lower() for h in header]
    for word in GUESSES[key]:
        for i, name in enumerate(names):
            if word in name:
                return i
    return fallback


def _statement() -> None:
    c = get_container()
    st.caption(
        "Bankanızdan veya kredi kartınızdan aldığınız CSV dökümünü yükleyin, kolonları eşleyin, kategorileri "
        "kontrol edip içe aktarın. Açıklamada geçen metne göre kategori atayan kuralları **Kategoriler & "
        "kurallar** sekmesinden tanımlayabilirsiniz. Aynı tarih, tutar ve açıklamayla kayıtlı satırlar mükerrer "
        "sayılır."
    )
    upload = st.file_uploader("Döküm (CSV)", type=["csv", "txt"], key="stmt_file")
    if upload is None:
        return
    try:
        header, rows = read_table(upload.getvalue())
    except DivitrackError as exc:
        st.error(str(exc))
        return
    idx = list(range(len(header)))
    optional = [None, *idx]

    def name(i: int | None) -> str:
        return "—" if i is None else header[i]

    col1, col2, col3 = st.columns(3)
    date_col = col1.selectbox("Tarih kolonu", idx, index=_guess(header, "date", 0) or 0, format_func=name)
    desc_col = col2.selectbox(
        "Açıklama kolonu", idx, index=_guess(header, "description", min(1, len(idx) - 1)) or 0, format_func=name
    )
    has_split = _guess(header, "debit", None) is not None and _guess(header, "credit", None) is not None
    mode = col3.radio("Tutar", ["Tek kolon", "Borç / Alacak"], index=1 if has_split else 0)
    if mode == "Tek kolon":
        col1, col2 = st.columns(2)
        amount_col = col1.selectbox(
            "Tutar kolonu", idx, index=_guess(header, "amount", len(idx) - 1) or 0, format_func=name
        )
        positive = col2.checkbox(
            "Giderler artı yazılmış", help="Kredi kartı dökümlerinde harcamalar genellikle eksi işaretsizdir"
        )
        cols = ColumnMap(date=date_col, description=desc_col, amount=amount_col, expense_positive=positive)
    else:
        col1, col2 = st.columns(2)
        debit = col1.selectbox(
            "Borç (gider)", optional, index=optional.index(_guess(header, "debit", None)), format_func=name
        )
        credit = col2.selectbox(
            "Alacak (gelir)", optional, index=optional.index(_guess(header, "credit", None)), format_func=name
        )
        cols = ColumnMap(date=date_col, description=desc_col, debit=debit, credit=credit)
    try:
        parsed = parse_statement(rows, cols)
    except DivitrackError as exc:
        st.error(str(exc))
        return
    if not parsed:
        st.warning("Seçilen kolonlarla okunabilen satır yok. Tarih ve tutar kolonlarını kontrol edin.")
        return

    income = c.budget.list_categories(FlowKind.INCOME)
    expense = c.budget.list_categories(FlowKind.EXPENSE)
    col1, col2 = st.columns(2)
    default_inc = col1.selectbox("Eşleşmeyen gelirler", income, format_func=lambda x: x.name, key="stmt_inc")
    default_exp = col2.selectbox(
        "Eşleşmeyen giderler",
        expense,
        index=next((i for i, x in enumerate(expense) if x.name.startswith("Diğer")), 0),
        format_func=lambda x: x.name,
        key="stmt_exp",
    )
    if default_inc is None or default_exp is None:
        st.warning("Önce gelir ve gider kategorisi tanımlayın.")
        return

    by_id = {x.id: x for x in [*income, *expense]}
    by_name = {x.name: x for x in [*income, *expense]}
    preview = c.budget.preview_statement(parsed)

    def auto(item) -> str:  # type: ignore[no-untyped-def]
        cat = by_id.get(item.category_id)
        kind = FlowKind.EXPENSE if item.row.is_expense else FlowKind.INCOME
        if cat is not None and cat.kind is kind:
            return cat.name
        return (default_exp if kind is FlowKind.EXPENSE else default_inc).name

    initial = [auto(item) for item in preview]
    frame = pd.DataFrame(
        {
            "Satır": [item.row.line for item in preview],
            "Tarih": [item.row.day for item in preview],
            "Açıklama": [item.row.description for item in preview],
            "Tutar": [float(item.row.amount) for item in preview],
            "Kategori": initial,
            "Kural": ["✓" if item.category_id is not None else "" for item in preview],
            "Mükerrer": [item.duplicate for item in preview],
        }
    )
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        disabled=["Satır", "Tarih", "Açıklama", "Tutar", "Kural", "Mükerrer"],
        column_config={
            "Satır": None,
            "Tarih": st.column_config.DateColumn(format="DD.MM.YYYY"),
            "Tutar": NUM(format="%.2f"),
            "Kategori": st.column_config.SelectboxColumn(options=sorted(by_name), required=True),
        },
        key="stmt_editor",
    )
    dupes = sum(item.duplicate for item in preview)
    total_in = sum(float(item.row.amount) for item in preview if not item.row.is_expense)
    total_out = -sum(float(item.row.amount) for item in preview if item.row.is_expense)
    st.caption(
        f"{len(preview)} satır · gelir {fmt_money(total_in, TRY)} · gider {fmt_money(total_out, TRY)}"
        + (f" · {dupes} mükerrer" if dupes else "")
        + ". Gider satırına gelir kategorisi seçilirse varsayılan gider kategorisi kullanılır."
    )
    skip = st.checkbox("Mükerrer satırları atla", value=True)
    if st.button("Dökümü içe aktar", type="primary"):
        overrides = {
            int(line): by_name[chosen].id or -1
            for line, chosen, first in zip(edited["Satır"], edited["Kategori"], initial, strict=True)
            if chosen != first and chosen in by_name
        }
        run_action(
            lambda: flash(
                str(
                    c.budget.import_statement(
                        parsed,
                        income_category_id=default_inc.id or -1,
                        expense_category_id=default_exp.id or -1,
                        overrides=overrides,
                        skip_duplicates=skip,
                    )
                )
            )
        )


def _rules() -> None:
    c = get_container()
    st.markdown("**Döküm eşleme kuralları**")
    st.caption(
        "Banka dökümündeki açıklama bu metni içeriyorsa satır o kategoriye atanır (ör. *migros* → Market, "
        "*shell* → Ulaşım). Büyük/küçük harf fark etmez; uzun kurallar önce denenir."
    )
    cats = {x.id: x for x in c.budget.list_categories()}
    rules = c.budget.list_rules()
    if rules:
        st.dataframe(
            pd.DataFrame([{"Metin": p, "Kategori": cats[cid].name if cid in cats else "?"} for _, p, cid in rules]),
            hide_index=True,
            width="stretch",
        )
    col1, col2 = st.columns(2)
    with col1, st.form("rule_add", clear_on_submit=True):
        pattern = st.text_input("Açıklamada geçen metin", placeholder="migros")
        cat = st.selectbox("Kategori", list(cats.values()), format_func=lambda x: f"{x.name} ({x.kind.label})")
        if st.form_submit_button("Kural ekle") and cat is not None:
            run_action(lambda: c.budget.add_rule(pattern, cat.id or -1), "Kural eklendi")
    if rules:
        with col2, st.form("rule_del"):
            pick = st.selectbox("Silinecek kural", rules, format_func=lambda r: f"{r[1]} → {cats[r[2]].name}")
            if st.form_submit_button("Kuralı sil") and pick is not None:
                run_action(lambda: c.budget.delete_rule(pick[0]), "Kural silindi")
