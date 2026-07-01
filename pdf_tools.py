"""
PDF Tools — iLovePDF-style utility app built with Streamlit.

Features:
  1. Merge PDFs
  2. Split PDF
  3. Edit PDF (remove pages / insert pages)
  4. PDF to Word
"""

import base64
import io
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

from tools.merger import merge_pdfs, get_page_count
from tools.splitter import (
    split_by_ranges,
    split_every_n_pages,
    extract_pages,
    split_all_pages,
    pack_zip,
)
from tools.editor import remove_pages, insert_pdf, reorder_pages
from tools.converter import pdf_to_word

st.set_page_config(
    page_title="PDF Tools",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] { background: #f8f9fb; }
[data-testid="stSidebar"] { background: #1a1a2e; }
[data-testid="stSidebar"] * { color: #e0e0e0 !important; }
.tool-card { background: white; border-radius: 16px; padding: 2rem 1.5rem; text-align: center;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08); cursor: pointer; transition: transform .15s, box-shadow .15s;
    min-height: 160px; display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 0.75rem; }
.tool-card:hover { transform: translateY(-4px); box-shadow: 0 8px 24px rgba(0,0,0,0.14); }
.tool-icon { font-size: 2.8rem; line-height: 1; }
.tool-title { font-size: 1.1rem; font-weight: 700; color: #1a1a2e; }
.tool-desc { font-size: 0.82rem; color: #666; }
.card-blue { border-top: 4px solid #3a86ff; }
.card-orange { border-top: 4px solid #fb8500; }
.card-purple { border-top: 4px solid #7b2d8b; }
.card-green { border-top: 4px solid #06a77d; }
.page-header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border-radius: 14px; padding: 1.5rem 2rem; margin-bottom: 1.5rem; color: white; }
.page-header h2 { color: white; margin: 0; }
.page-header p { color: #aaa; margin: 0.3rem 0 0; font-size: 0.9rem; }
.file-pill { background: white; border: 1px solid #e0e0e0; border-radius: 10px;
    padding: 0.6rem 1rem; margin-bottom: 0.5rem; display: flex; align-items: center;
    gap: 0.75rem; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
.step-label { background: #3a86ff; color: white; border-radius: 50%;
    width: 26px; height: 26px; display: inline-flex; align-items: center;
    justify-content: center; font-weight: 700; font-size: 0.8rem; margin-right: 0.5rem; }
.stButton > button[kind="primary"] { background: #3a86ff; border: none; border-radius: 8px;
    padding: 0.7rem 2rem; font-size: 1rem; font-weight: 600; color: white; width: 100%; }
.stDownloadButton > button { background: #06a77d; border: none; border-radius: 8px;
    padding: 0.7rem 2rem; font-size: 1rem; font-weight: 600; color: white; width: 100%; }
.info-box { background: #e8f0fe; border-left: 4px solid #3a86ff;
    border-radius: 0 8px 8px 0; padding: 0.75rem 1rem; font-size: 0.88rem;
    color: #1a1a2e; margin: 0.5rem 0; }
</style>
""",
    unsafe_allow_html=True,
)


def fmt_size(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1024**2:
        return f"{n_bytes/1024:.1f} KB"
    return f"{n_bytes/1024**2:.1f} MB"


def pdf_preview_html(pdf_bytes: bytes, height: int = 500) -> str:
    b64 = base64.b64encode(pdf_bytes).decode()
    return (
        f'<div style="border:1px solid #e0e0e0;border-radius:10px;overflow:hidden;">'
        f'<iframe src="data:application/pdf;base64,{b64}" '
        f'width="100%" height="{height}px" '
        f'style="border:none;"></iframe></div>'
    )


def section_header(icon: str, title: str, subtitle: str = "") -> None:
    sub = f"<p>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f'<div class="page-header"><h2>{icon} {title}</h2>{sub}</div>',
        unsafe_allow_html=True,
    )


def step(n: int, text: str) -> None:
    st.markdown(
        f'<p><span class="step-label">{n}</span><strong>{text}</strong></p>',
        unsafe_allow_html=True,
    )


PAGES = {
    "🏠 Home": "Home",
    "🔀 Merge PDF": "Merge PDF",
    "✂️ Split PDF": "Split PDF",
    "📝 Edit PDF": "Edit PDF",
    "📃 PDF to Word": "PDF to Word",
}

if "page" not in st.session_state:
    st.session_state.page = "Home"


def build_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            "<h2 style='color:white;margin-bottom:0.25rem;'>📄 PDF Tools</h2>"
            "<p style='color:#aaa;font-size:0.8rem;margin-top:0;'>v1.0</p>",
            unsafe_allow_html=True,
        )
        st.markdown("---")
        for label, page_key in PAGES.items():
            if st.button(label, key=f"nav_{page_key}", use_container_width=True):
                st.session_state.page = page_key
                for key in ["merge_result", "split_result", "edit_result", "conv_result"]:
                    st.session_state.pop(key, None)
                st.rerun()
        st.markdown("---")
        st.markdown(
            "<p style='color:#666;font-size:0.75rem;text-align:center;'>Built with Streamlit + pypdf</p>",
            unsafe_allow_html=True,
        )


def main() -> None:
    build_sidebar()
    page = st.session_state.page
    if page == "Home":
        st.markdown(
            '<div style="text-align:center;padding:2rem 0 1.5rem;">'
            '<h1 style="font-size:2.4rem;color:#1a1a2e;margin:0;">📄 PDF Tools</h1>'
            '<p style="color:#666;font-size:1.05rem;margin-top:0.5rem;">'
            'All-in-one PDF utilities — merge, split, edit, and convert</p></div>',
            unsafe_allow_html=True,
        )
        st.info("💡 Select any tool from the sidebar to get started.")
    else:
        st.info(f"Navigate to {page} using the sidebar.")


if __name__ == "__main__":
    main()
