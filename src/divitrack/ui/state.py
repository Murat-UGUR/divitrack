"""Streamlit oturum yardımcıları: container önbelleği, flash mesajlar, ortak veri yükleme."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

from divitrack.bootstrap import Container, build_container
from divitrack.core.exceptions import DivitrackError

FLASH_KEY = "_flash"


@st.cache_resource(show_spinner=False)
def get_container() -> Container:
    return build_container()


def flash(message: str, kind: str = "success") -> None:
    """Bir sonraki çalıştırmada gösterilecek mesaj (st.rerun sonrası kaybolmasın diye)."""
    st.session_state.setdefault(FLASH_KEY, []).append((kind, message))


def show_flashes() -> None:
    for kind, message in st.session_state.pop(FLASH_KEY, []):
        getattr(st, kind, st.info)(message)


def run_action(fn: Callable[[], Any], success: str | None = None, *, rerun: bool = True) -> Any:
    """Bir servis çağrısını çalıştırır; domain hatalarını kullanıcı dostu mesaja çevirir."""
    try:
        result = fn()
    except DivitrackError as exc:
        st.error(str(exc))
        return None
    except Exception as exc:  # beklenmeyen hata — kullanıcıya göster, uygulamayı çökertme
        st.exception(exc)
        return None
    if success:
        flash(success)
    if rerun:
        st.rerun()
    return result
