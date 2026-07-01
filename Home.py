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

st.set_page_config(page_title="Sangir Analytics · Tools", page_icon="🧰", layout="wide")
inject_css()


# ── tool catalogue ──────────────────────────────────────────────────────────────
_TOOLS = [
    {
        "group": "Data Engineering",
        "icon": "🗄️", "title": "Disha Parquet Tool", "tag": "Streamlit",
        "desc": "CSV/Excel ↔ Parquet conversion, schema viewer, CSV utilities, DuckDB SQL analytics.",
    },
    {
        "group": "Documents & PDFs",
        "icon": "📄", "title": "PDF Tools", "tag": "Streamlit",
        "desc": "Merge, split, reorder / remove / insert pages, and convert PDF to Word.",
    },
    {
        "group": "Documents & PDFs",
        "icon": "🔒", "title": "Document Redaction", "tag": "Desktop app",
        "desc": "Auto-redact PAN, TAN, GSTIN, CIN, Aadhaar, phone, email across PDF, DOCX, XLSX, and images.",
    },
    {
        "group": "Statutory & Payroll",
        "icon": "🧾", "title": "PF & Statutory", "tag": "Streamlit",
        "desc": "PF Challan / ECR / TRRN, plus ESI · PT · TDS · GSTR-1 · GSTR-3B extraction and reconciliation.",
    },
    {
        "group": "Finance & Loan Audit",
        "icon": "📊", "title": "SOA · RPS · Reconcile", "tag": "Flask app",
        "desc": "L&T Finance SOA extractor with TOC/TOD validation, RPS parser, and SOA-vs-RPS reconciliation.",
    },
    {
        "group": "Finance & Loan Audit",
        "icon": "🔍", "title": "JE Audit Analytics", "tag": "Streamlit",
        "desc": "Journal Entry exception testing — amount, timing, user, vendor, and Benford's Law checks.",
    },
    {
        "group": "Finance & Loan Audit",
        "icon": "⚡", "title": "JE Audit (Blazor)", "tag": ".NET / Blazor",
        "desc": "Same JE audit engine as a self-contained Blazor Server app, for .NET deployments.",
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
            <h1>🧰 Sangir Analytics — Tools</h1>
            <p>A unified workspace for loan-audit, statutory-compliance, and document-processing
            tools — everything runs locally, nothing is uploaded to a server you don't control.</p>
            <div class="sa-badges">
                <span class="sa-badge">7 tools</span>
                <span class="sa-badge">Local-only processing</span>
                <span class="sa-badge">Streamlit · Flask · Desktop · .NET</span>
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
            st.Page("_pages/parquet.py", title="Disha Parquet Tool", icon="🗄️"),
            st.Page("_pages/pf_statutory.py", title="PF & Statutory", icon="🧾"),
            st.Page("_pages/pdf_tools_page.py", title="PDF Tools", icon="📄"),
            st.Page("_pages/soa.py", title="SOA · RPS · Reconcile", icon="📊"),
            st.Page("_pages/redaction.py", title="Document Redaction", icon="🔒"),
            st.Page("_pages/je_audit.py", title="JE Audit Analytics", icon="🔍"),
            st.Page("_pages/je_audit_blazor.py", title="JE Audit (Blazor)", icon="⚡"),
        ],
    }
)
nav.run()
