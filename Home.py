"""
Sangir Analytics — unified Tools hub.

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
import launcher  # noqa: E402 — reused for the manual "check for updates" control below

st.set_page_config(page_title="Sangir Analytics · Tools", page_icon="🧰", layout="wide")
inject_css()


# ── on-open update notification ─────────────────────────────────────────────
# launcher.py (Uzumaki.exe's entry point) sets these two env vars after its
# self-update check, before handing off to Streamlit. When run from source
# via `streamlit run Home.py` directly (no launcher involved) they're absent,
# so this defaults to a quiet "current, unknown version" toast.
_UPDATE_MESSAGES = {
    "updated": ("Uzumaki updated to v{v}", "✅"),
    "current": ("Uzumaki v{v} — you're on the latest version", "🍥"),
    "offline": ("Uzumaki v{v} — couldn't check for updates (offline)", "📴"),
    "update_failed": ("Uzumaki v{v} — an update is available but the download failed", "⚠️"),
}


def _update_notice(status: str, version: str) -> tuple[str, str]:
    """Pure (status, version) -> (message, icon) for the on-open toast. No
    Streamlit calls here so this stays unit-testable without a live session."""
    template, icon = _UPDATE_MESSAGES.get(status, _UPDATE_MESSAGES["current"])
    return template.format(v=version), icon


if "_update_notice_shown" not in st.session_state:
    st.session_state["_update_notice_shown"] = True
    _msg, _icon = _update_notice(
        os.environ.get("UZUMAKI_UPDATE_STATUS", "current"),
        os.environ.get("UZUMAKI_VERSION", "dev"),
    )
    st.toast(_msg, icon=_icon)


# ── manual "check for updates now" control (sidebar, visible on every page) ─
def _check_for_updates_now() -> None:
    """Button handler — check GitHub immediately instead of waiting for the
    next launch, and self-update+relaunch in place if a newer build exists.

    This runs inside Streamlit's per-session script-runner thread, not the
    main thread. launcher._self_update_and_relaunch() ends with a plain
    sys.exit(0) — fine when launcher.py calls it at startup (main thread,
    kills the whole process), but a SystemExit raised in a *non-main* thread
    only kills that thread, leaving the frozen .exe still running and the
    relaunch helper script stuck waiting to delete a file that's still
    locked. Catching it here and forcing os._exit(0) achieves the same full
    process exit regardless of which thread triggered it.
    """
    if not launcher._is_frozen():
        st.info("Running from source — pull the latest with `git pull`.")
        return
    with st.spinner("Checking GitHub for a newer build…"):
        remote = launcher._remote_version()
    local = launcher._local_version()
    if remote is None:
        st.warning("Couldn't reach GitHub — check your connection and try again.")
    elif remote == local:
        st.success(f"You're already on the latest version (v{local}).")
    else:
        st.info(f"Update found (v{remote}) — downloading and restarting…")
        try:
            launcher._self_update_and_relaunch()
        except SystemExit:
            os._exit(0)


with st.sidebar:
    st.divider()
    st.caption(f"Uzumaki v{os.environ.get('UZUMAKI_VERSION', launcher._local_version())}")
    if st.button("🔄 Check for updates", use_container_width=True):
        _check_for_updates_now()


# ── tool catalogue ──────────────────────────────────────────────────────────────
_TOOLS = [
    {
        "group": "Data Engineering",
        "icon": "🗄️", "title": "Uzumaki Tool", "tag": "Streamlit",
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
]

_GROUP_ORDER = ["Finance & Loan Audit", "Statutory & Payroll", "Documents & PDFs", "Data Engineering"]

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
                <span class="sa-badge">7 tools</span>
                <span class="sa-badge">Local-only processing</span>
                <span class="sa-badge">One framework — Streamlit</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
        "Home": [st.Page(home, title="Home", icon="🏠", default=True)],
        "Tools": [
            st.Page("_pages/parquet.py", title="Uzumaki Tool", icon="🗄️"),
            st.Page("_pages/pf_statutory.py", title="PF & Statutory", icon="🧾"),
            st.Page("_pages/form26as_page.py", title="Form 26AS Extractor", icon="🧮"),
            st.Page("_pages/pdf_tools_page.py", title="PDF Tools", icon="📄"),
            st.Page("_pages/soa.py", title="SOA · RPS · Reconcile", icon="📊"),
            st.Page("_pages/redaction.py", title="Document Redaction", icon="🔒"),
            st.Page("_pages/je_audit.py", title="JE Audit Analytics", icon="🔍"),
        ],
    }
)
nav.run()
