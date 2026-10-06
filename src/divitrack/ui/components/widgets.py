"""Tekrar kullanılan arayüz parçaları."""

from __future__ import annotations

import streamlit as st

from divitrack.bootstrap import Container
from divitrack.core.enums import Currency
from divitrack.ui.formatting import fmt_pct

CURRENCIES = list(Currency)


def sidebar(c: Container) -> Currency:
    with st.sidebar:
        st.markdown("### DiviTrack")
        current = c.base_currency
        choice = st.selectbox(
            "Raporlama para birimi",
            CURRENCIES,
            index=CURRENCIES.index(current),
            format_func=lambda x: f"{x.symbol} {x.value}",
            key="base_ccy",
        )
        if choice is not current:
            c.set_base_currency(choice)
            st.rerun()
        if st.button("↻ Fiyatları yenile", help="Önbelleği atlayıp güncel fiyatları çeker"):
            st.session_state["force_refresh"] = True
            st.rerun()
        if not c.market.online:
            st.caption("🔌 Çevrimdışı mod — fiyatlar önbellekten/manuel")
        elif c.market.last_error:
            st.caption(f"⚠️ Son fiyat hatası: {c.market.last_error[:120]}")
        st.caption("Veriler yalnızca bu bilgisayarda saklanır.")
    return choice


def consume_force_refresh() -> bool:
    return bool(st.session_state.pop("force_refresh", False))


def delta_pct(value: object) -> str | None:
    return None if value is None else fmt_pct(value, signed=True)  # type: ignore[arg-type]
