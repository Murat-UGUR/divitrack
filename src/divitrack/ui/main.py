"""Streamlit uygulama kabuğu: sayfa ayarı ve navigasyon."""

from __future__ import annotations

import streamlit as st

from divitrack.ui.pages import analysis, budget, dashboard, dividends, holdings, plan, settings


def run() -> None:
    st.set_page_config(page_title="DiviTrack", page_icon="📈", layout="wide")
    nav = st.navigation(
        {
            "Portföy": [
                st.Page(dashboard.render, title="Portföy Özeti", icon="📊", url_path="ozet", default=True),
                st.Page(holdings.render, title="Varlıklar & İşlemler", icon="💼", url_path="varliklar"),
                st.Page(dividends.render, title="Temettüler", icon="💰", url_path="temettu"),
                st.Page(analysis.render, title="Analiz", icon="🧭", url_path="analiz"),
            ],
            "Bütçe": [
                st.Page(budget.render, title="Bütçe", icon="🧾", url_path="butce"),
                st.Page(plan.render, title="Aylık Plan", icon="🗓️", url_path="plan"),
            ],
            "Sistem": [
                st.Page(settings.render, title="Ayarlar & Veri", icon="⚙️", url_path="ayarlar"),
            ],
        }
    )
    nav.run()
