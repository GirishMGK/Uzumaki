"""Shared Streamlit widgets for the Tally hub pages.

render_connection_picker() extracts the host/port/Test Connection/company-
selectbox block that used to be duplicated almost verbatim between the Live
and Sales & Purchase Register tabs in _pages/tally_extractions.py -- every
live-pull page (that one, plus the new GST Summary, TDS Summary, and
Registers pages) calls this instead of reimplementing it again.
"""
import datetime
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tally_tool"))
import tally_connector


def _current_fy_start(today: datetime.date | None = None) -> datetime.date:
    """Start of the current Indian financial year (1 April) as a sane
    default for the live-pull date pickers -- previously hardcoded to
    2000-01-01 across every Tally live-pull page, a 26-year span that made a
    routine pull genuinely take Tally long enough to trip the request
    timeout (confirmed live, on the Tally extraction tool page specifically
    -- fixed there first, then found to still be present, unfixed, on every
    OTHER Tally live-pull page too, since each was written independently
    with its own copy of the same literal default). Most audit/statutory
    work is done one FY at a time anyway; a 26-year default served nobody
    and actively broke the common case."""
    today = today or datetime.date.today()
    year = today.year if today.month >= 4 else today.year - 1
    return datetime.date(year, 4, 1)


def render_connection_picker(key_prefix: str) -> tuple[str, int, str | None]:
    """Renders host/port inputs, a Test Connection button, and a company
    picker (a selectbox once Test Connection has populated the list, a plain
    text input otherwise). `key_prefix` namespaces the Streamlit widget keys
    and session_state entry so multiple pages/tabs on screen at once (or the
    same page rendering this twice) don't collide.

    Returns (host, port, company) ready to hand straight to any
    tally_connector fetch function."""
    c1, c2 = st.columns([3, 1])
    with c1:
        host = st.text_input("Tally host", value="localhost", key=f"{key_prefix}_host")
    with c2:
        port = st.number_input(
            "Port", value=tally_connector.DEFAULT_PORT, min_value=1, max_value=65535, step=1,
            key=f"{key_prefix}_port",
        )

    companies_key = f"{key_prefix}_companies"
    if companies_key not in st.session_state:
        st.session_state[companies_key] = []

    if st.button("Test Connection", key=f"{key_prefix}_test"):
        with st.spinner("Contacting Tally…"):
            ok, message = tally_connector.test_connection(host, int(port))
        if ok:
            st.success(message)
            try:
                st.session_state[companies_key] = tally_connector.list_companies(host, int(port))
            except tally_connector.TallyConnectionError:
                st.session_state[companies_key] = []
        else:
            st.error(message)
            st.session_state[companies_key] = []

    if st.session_state[companies_key]:
        company = st.selectbox("Company", st.session_state[companies_key], key=f"{key_prefix}_company_sel")
    else:
        company = st.text_input(
            "Company name (optional — leave blank to use whichever company is currently open)",
            key=f"{key_prefix}_company_manual",
        ) or None

    return host, int(port), company


def render_setup_help(expanded: bool = False) -> None:
    """The one-time-per-Tally-session setup instructions -- shared verbatim
    across every live-pull page so they don't drift out of sync."""
    with st.expander("Setup (one-time per Tally session)", expanded=expanded):
        st.markdown(
            """
1. Restore/open the backup **inside TallyPrime itself** — nothing outside Tally
   can read its backup format directly.
2. **F12** (Configure) → **Advanced Configuration** → enable **ODBC/XML Server**
   (older versions: "Client/Server Configuration"), note the port (default **9000**).
3. Keep Tally open with the company loaded for the duration of the pull.
4. Uzumaki and Tally must be on the **same machine** (or reachable over the network) —
   click **Test Connection** below once Tally is ready.
"""
        )
