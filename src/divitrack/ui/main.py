"""Streamlit uygulama kabuğu: sayfa ayarı ve navigasyon."""

from __future__ import annotations

import streamlit as st

from divitrack.ui.pages import analysis, budget, dashboard, dividends, holdings, plan, settings
from divitrack.ui.state import get_container


def _unlocked() -> bool:
    """Parola tanımlıysa oturum açılana kadar kilit ekranı gösterir."""
    security = get_container().security
    if not security.has_password or st.session_state.get("unlocked"):
        return True
    st.title("🔒 DiviTrack")
    with st.form("unlock"):
        password = st.text_input("Parola", type="password")
        if st.form_submit_button("Aç", type="primary"):
            if security.verify(password):
                st.session_state["unlocked"] = True
                st.rerun()
            st.error("Parola yanlış")
    return False


def run() -> None:
    st.set_page_config(page_title="DiviTrack", page_icon="📈", layout="wide")
    if not _unlocked():
        return
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
