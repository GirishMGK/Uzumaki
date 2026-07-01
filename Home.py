"""
Sangir Analytics — unified Tools hub.

A single Streamlit application that groups every tool under a **Tools** section
in the sidebar; each tool opens as its own page.

Run:
    streamlit run Home.py
"""

from __future__ import annotations

import os
import sys

import streamlit as st

# make repo root importable for the page scripts (parquet_tool, tools.*, etc.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

st.set_page_config(page_title="Sangir Analytics · Tools", page_icon="🧰", layout="wide")


# ── landing page ───────────────────────────────────────────────────────────────
def home():
    st.title("Sangir Analytics — Tools")
    st.caption("Pick a tool from the **Tools** group in the sidebar, or from the cards below.")

    cards = [
        ("🗄️", "Disha Parquet Tool", "CSV/Excel ↔ Parquet, viewer, CSV tools, DuckDB analytics."),
        ("🧾", "PF & Statutory", "PF Challan/ECR/TRRN + ESI/PT/TDS/GSTR-1/GSTR-3B extraction."),
        ("📄", "PDF Tools", "Merge, split, edit and convert PDFs to Word."),
        ("📊", "SOA · RPS · Reconcile", "L&T Finance SOA extractor with TOC/TOD audit (Flask app)."),
        ("🔒", "Document Redaction", "Auto-redact PAN/TAN/GSTIN/CIN/Aadhaar/Phone/Email — PDF, DOCX, XLSX, images (desktop app)."),
    ]
    cols = st.columns(2)
    for i, (icon, title, desc) in enumerate(cards):
        with cols[i % 2]:
            st.markdown(
                f"""
                <div style="background:#fff;border:1px solid #eee;border-radius:14px;
                            padding:1.2rem 1.4rem;margin-bottom:1rem;
                            box-shadow:0 2px 10px rgba(0,0,0,.06);">
                    <div style="font-size:2rem">{icon}</div>
                    <div style="font-weight:700;font-size:1.1rem;margin:.3rem 0">{title}</div>
                    <div style="color:#555;font-size:.9rem">{desc}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


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
        ],
    }
)
nav.run()
