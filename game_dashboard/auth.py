"""Optional password gate driven by Streamlit secrets."""

from __future__ import annotations

import hmac

import streamlit as st


def check_password() -> bool:
    """Require a password when ``app_password`` is defined in Streamlit secrets.

    Returns:
        True when no password is configured or the correct password was entered
        in this session; False otherwise (a password prompt is rendered).
    """
    try:
        expected = st.secrets.get("app_password")
    except (FileNotFoundError, KeyError):
        expected = None
    if not expected:
        return True
    if st.session_state.get("authenticated"):
        return True

    entered = st.text_input("Password", type="password", key="password_input")
    if entered:
        if hmac.compare_digest(entered.encode("utf-8"), str(expected).encode("utf-8")):
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("Incorrect password.")
    return False
