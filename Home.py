"""
Uzumaki — unified Tools hub.

A single Streamlit application that groups every tool under a **Tools** section
in the sidebar; each tool opens as its own page.

Run:
    streamlit run Home.py
"""

from __future__ import annotations

import importlib
import os
import sys

import streamlit as st

# make repo root importable for the page scripts (parquet_tool, tools.*, etc.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _pages.theme import inject_css, footer  # noqa: E402
import updater  # noqa: E402

st.set_page_config(page_title="Uzumaki · Tools", page_icon="🧰", layout="wide")
inject_css()


# ── sidebar: check for updates ──────────────────────────────────────────────
def _render_update_sidebar() -> None:
    """Placed here (not a page) so it runs on every page load, since Home.py
    is st.navigation()'s entry script and executes on each navigation --
    the sidebar it renders into persists across pages within one session.
    Only meaningful when running as the packaged .exe (there's nothing to
    self-update when running from source); check_update_status() reports
    that itself so this just renders whatever it says."""
    with st.sidebar:
        st.markdown("---")
        with st.expander("🔄 Check for Updates", expanded=False):
            if "update_status" not in st.session_state:
                st.session_state.update_status = updater.check_update_status()
            status = st.session_state.update_status

            if not updater.is_frozen():
                st.caption("Running from source — nothing to self-update here.")
            else:
                st.caption(f"Current version: `{status['local']}`")
                if st.button("Check now", key="check_updates_btn", use_container_width=True):
                    with st.spinner("Checking…"):
                        st.session_state.update_status = updater.check_update_status()
                    st.rerun()

                if status["checked"] and status["update_available"]:
                    st.success(f"Update available: `{status['remote']}`")
                    if st.button(
                        "⬇ Download & Restart", key="do_update_btn",
                        type="primary", use_container_width=True,
                    ):
                        progress_bar = st.progress(0, text="Downloading update…")

                        def _on_progress(downloaded: int, total: int | None) -> None:
                            # total is None if Tally/GitHub didn't send a
                            # Content-Length header -- fall back to showing
                            # bytes downloaded so far with no percentage,
                            # rather than crashing on a None comparison.
                            if total:
                                pct = min(downloaded / total, 1.0)
                                mb_done, mb_total = downloaded / 1e6, total / 1e6
                                progress_bar.progress(
                                    pct, text=f"Downloading update… {mb_done:.1f} / {mb_total:.1f} MB"
                                )
                            else:
                                mb_done = downloaded / 1e6
                                progress_bar.progress(0, text=f"Downloading update… {mb_done:.1f} MB")

                        ok, message = updater.perform_update_and_restart(on_progress=_on_progress)
                        # Only reached if it FAILED -- success replaces this
                        # whole process via os._exit(), no return.
                        if not ok:
                            progress_bar.empty()
                            st.error(message)
                elif status["checked"]:
                    st.caption("✅ You're on the latest version.")
                elif status["remote"] is None and status["local"] != "0.0.0-dev":
                    st.caption("Couldn't reach GitHub to check — offline?")

        st.markdown(
            '<div class="sa-credit">Built by <strong>Girish</strong></div>',
            unsafe_allow_html=True,
        )


# ── tool catalogue ──────────────────────────────────────────────────────────────
_TOOLS = [
    {
        "group": "Data Engineering",
        "icon": "🗄️", "title": "Parquet Tool", "tag": "Streamlit",
        "desc": "CSV/Excel ↔ Parquet conversion, schema viewer, CSV utilities, DuckDB SQL analytics.",
    },
    {
        "group": "Documents & PDFs",
        "icon": "📄", "title": "PDF Tools", "tag": "Streamlit",
        "desc": "Merge, split, reorder / remove / insert pages, and convert PDF to Word.",
    },
    {
        "group": "Documents & PDFs",
        "icon": "🔒", "title": "Document Redaction", "tag": "Streamlit",
        "desc": "Auto-redact PAN, TAN, GSTIN, CIN, Aadhaar, phone, email across PDF, DOCX, XLSX, and images.",
    },
    {
        "group": "Statutory & Payroll",
        "icon": "🧾", "title": "PF & Statutory", "tag": "Streamlit",
        "desc": "PF Challan / ECR / TRRN, plus ESI · PT · TDS · GSTR-1 · GSTR-3B extraction and reconciliation.",
    },
    {
        "group": "Statutory & Payroll",
        "icon": "🧮", "title": "Form 26AS Extractor", "tag": "Streamlit",
        "desc": "Flatten one or more Form 26AS downloads (Part A / TDS) into a single searchable, multi-year workbook.",
    },
    {
        "group": "Finance & Loan Audit",
        "icon": "📊", "title": "SOA · RPS · Reconcile", "tag": "Streamlit",
        "desc": "L&T Finance SOA extractor with TOC/TOD validation, RPS parser, and SOA-vs-RPS reconciliation.",
    },
    {
        "group": "Finance & Loan Audit",
        "icon": "🔍", "title": "JE Audit Analytics", "tag": "Streamlit",
        "desc": "Journal Entry exception testing — amount, timing, user, vendor, and Benford's Law checks.",
    },
    {
        "group": "Finance & Loan Audit",
        "icon": "📒", "title": "Tally extraction tool", "tag": "Streamlit",
        "desc": "Pull every ledger's full transaction history out of a Tally JSON export in one shot, with running balances and a control-total check.",
    },
    {
        "group": "Workforce & Scheduling",
        "icon": "🧑‍💼", "title": "Firm RMS", "tag": "FastAPI (in-process)",
        "desc": "Manpower/resource tracking — scheduler board, capacity dashboards, timesheets, forecasting. Has its own login and local database.",
    },
]

_GROUP_ORDER = ["Finance & Loan Audit", "Statutory & Payroll", "Documents & PDFs", "Data Engineering", "Workforce & Scheduling"]

_DEP_CHECKS = [
    ("pandas", "pandas"), ("pyarrow", "PyArrow"), ("duckdb", "DuckDB"),
    ("fitz", "PyMuPDF"), ("pdfplumber", "pdfplumber"), ("docx", "python-docx"),
    ("openpyxl", "openpyxl"), ("pytesseract", "Tesseract OCR (Python binding)"),
    ("sklearn", "scikit-learn"), ("scipy", "SciPy"), ("plotly", "Plotly"),
]


def _dep_status() -> list[tuple[str, bool]]:
    status = []
    for mod, label in _DEP_CHECKS:
        try:
            importlib.import_module(mod)
            status.append((label, True))
        except Exception:
            status.append((label, False))
    return status


# ── landing page ───────────────────────────────────────────────────────────────
def home():
    st.markdown(
        """
        <div class="sa-hero">
            <h1>🧰 Uzumaki — Tools</h1>
            <p>A unified workspace for loan-audit, statutory-compliance, and document-processing
            tools — everything runs locally, nothing is uploaded to a server you don't control.</p>
            <div class="sa-badges">
                <span class="sa-badge">9 tools</span>
                <span class="sa-badge">Local-only processing</span>
                <span class="sa-badge">One app — one .exe</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.page_link("_pages/about.py", label="🧩 Curious how this is built? See the tech behind it →")

    counts = {}
    for t in _TOOLS:
        counts[t["group"]] = counts.get(t["group"], 0) + 1
    kpi_html = "".join(
        f'<div class="sa-kpi"><div class="sa-kpi-val">{counts[g]}</div>'
        f'<div class="sa-kpi-lbl">{g}</div></div>'
        for g in _GROUP_ORDER
    )
    st.markdown(f'<div class="sa-kpi-row">{kpi_html}</div>', unsafe_allow_html=True)

    for group in _GROUP_ORDER:
        group_tools = [t for t in _TOOLS if t["group"] == group]
        st.markdown(f'<div class="sa-section">{group}</div>', unsafe_allow_html=True)
        cols = st.columns(len(group_tools) if len(group_tools) <= 3 else 3)
        for i, t in enumerate(group_tools):
            with cols[i % len(cols)]:
                st.markdown(
                    f"""
                    <div class="sa-card">
                        <div class="sa-card-icon">{t['icon']}</div>
                        <div class="sa-card-title">{t['title']}</div>
                        <div class="sa-card-desc">{t['desc']}</div>
                        <div class="sa-card-tag">{t['tag']}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.markdown('<div class="sa-section">Environment</div>', unsafe_allow_html=True)
    st.caption("Optional dependencies detected in this environment — install anything missing from `requirements.txt` to unlock the relevant tool features.")
    chips = "".join(
        f'<span class="sa-status {"ok" if ok else "warn"}"><span class="dot"></span>{label}</span>'
        for label, ok in _dep_status()
    )
    st.markdown(chips, unsafe_allow_html=True)

    footer()


# ── navigation: a "Tools" group with one page per tool ─────────────────────────
nav = st.navigation(
    {
        "Home": [
            st.Page(home, title="Home", icon="🏠", default=True),
            st.Page("_pages/about.py", title="How it's built", icon="🧩"),
        ],
        "Tools": [
            st.Page("_pages/parquet.py", title="Parquet Tool", icon="🗄️"),
            st.Page("_pages/pf_statutory.py", title="PF & Statutory", icon="🧾"),
            st.Page("_pages/form26as_page.py", title="Form 26AS Extractor", icon="🧮"),
            st.Page("_pages/pdf_tools_page.py", title="PDF Tools", icon="📄"),
            st.Page("_pages/soa.py", title="SOA · RPS · Reconcile", icon="📊"),
            st.Page("_pages/redaction.py", title="Document Redaction", icon="🔒"),
            st.Page("_pages/je_audit.py", title="JE Audit Analytics", icon="🔍"),
            st.Page("_pages/tally_extractions.py", title="Tally extraction tool", icon="📒"),
            st.Page("_pages/firm_rms.py", title="Firm RMS", icon="🧑‍💼"),
        ],
    }
)
_render_update_sidebar()
nav.run()
