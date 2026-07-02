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

# ── path setup ────────────────────────────────────────────────────────────────
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

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PDF Tools",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
/* ── global ── */
[data-testid="stAppViewContainer"] {
    background: #f8f9fb;
}
[data-testid="stSidebar"] {
    background: #1a1a2e;
}
[data-testid="stSidebar"] * {
    color: #e0e0e0 !important;
}
[data-testid="stSidebar"] .stRadio > label {
    color: #ffffff !important;
    font-weight: 600;
    font-size: 1rem;
}

/* ── tool cards ── */
.tool-card {
    background: white;
    border-radius: 16px;
    padding: 2rem 1.5rem;
    text-align: center;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    cursor: pointer;
    transition: transform .15s, box-shadow .15s;
    min-height: 160px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
}
.tool-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.14);
}
.tool-icon {
    font-size: 2.8rem;
    line-height: 1;
}
.tool-title {
    font-size: 1.1rem;
    font-weight: 700;
    color: #1a1a2e;
}
.tool-desc {
    font-size: 0.82rem;
    color: #666;
}

/* ── card colour accents ── */
.card-blue   { border-top: 4px solid #3a86ff; }
.card-orange { border-top: 4px solid #fb8500; }
.card-purple { border-top: 4px solid #7b2d8b; }
.card-green  { border-top: 4px solid #06a77d; }

/* ── page header ── */
.page-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border-radius: 14px;
    padding: 1.5rem 2rem;
    margin-bottom: 1.5rem;
    color: white;
}
.page-header h2 { color: white; margin: 0; }
.page-header p  { color: #aaa; margin: 0.3rem 0 0; font-size: 0.9rem; }

/* ── file pill ── */
.file-pill {
    background: white;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    padding: 0.6rem 1rem;
    margin-bottom: 0.5rem;
    display: flex;
    align-items: center;
    gap: 0.75rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}

/* ── step label ── */
.step-label {
    background: #3a86ff;
    color: white;
    border-radius: 50%;
    width: 26px; height: 26px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 0.8rem;
    margin-right: 0.5rem;
}

/* ── big action button ── */
.stButton > button[kind="primary"] {
    background: #3a86ff;
    border: none;
    border-radius: 8px;
    padding: 0.7rem 2rem;
    font-size: 1rem;
    font-weight: 600;
    color: white;
    width: 100%;
}
.stButton > button[kind="primary"]:hover {
    background: #2667cc;
}

/* ── download button ── */
.stDownloadButton > button {
    background: #06a77d;
    border: none;
    border-radius: 8px;
    padding: 0.7rem 2rem;
    font-size: 1rem;
    font-weight: 600;
    color: white;
    width: 100%;
}
.stDownloadButton > button:hover {
    background: #048060;
}

/* ── PDF preview frame ── */
.pdf-preview-wrap {
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    overflow: hidden;
}

/* ── info box ── */
.info-box {
    background: #e8f0fe;
    border-left: 4px solid #3a86ff;
    border-radius: 0 8px 8px 0;
    padding: 0.75rem 1rem;
    font-size: 0.88rem;
    color: #1a1a2e;
    margin: 0.5rem 0;
}
</style>
""",
    unsafe_allow_html=True,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def read_pdf_or_error(uploaded):
    """Read an uploaded file and return (bytes, page_count), or (None, None)
    after showing a friendly error if it isn't a readable PDF."""
    pdf_bytes = uploaded.read()
    try:
        return pdf_bytes, get_page_count(pdf_bytes)
    except Exception:
        st.error(f"'{uploaded.name}' doesn't look like a valid PDF (it may be corrupted, "
                  "empty, or password-protected).")
        return None, None


def fmt_size(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1024**2:
        return f"{n_bytes/1024:.1f} KB"
    return f"{n_bytes/1024**2:.1f} MB"


def pdf_preview_html(pdf_bytes: bytes, height: int = 500) -> str:
    b64 = base64.b64encode(pdf_bytes).decode()
    return (
        f'<div class="pdf-preview-wrap">'
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


# ── HOME PAGE ─────────────────────────────────────────────────────────────────

def page_home() -> None:
    st.markdown(
        """
        <div style="text-align:center; padding: 2rem 0 1.5rem;">
            <h1 style="font-size:2.4rem; color:#1a1a2e; margin:0;">📄 PDF Tools</h1>
            <p style="color:#666; font-size:1.05rem; margin-top:0.5rem;">
                All-in-one PDF utilities — merge, split, edit, and convert
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown(
            """
            <div class="tool-card card-blue">
                <div class="tool-icon">🔀</div>
                <div class="tool-title">Merge PDF</div>
                <div class="tool-desc">Combine multiple PDFs into one file</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Open Merge PDF", key="go_merge", use_container_width=True):
            st.session_state.page = "Merge PDF"
            st.rerun()

    with col2:
        st.markdown(
            """
            <div class="tool-card card-orange">
                <div class="tool-icon">✂️</div>
                <div class="tool-title">Split PDF</div>
                <div class="tool-desc">Split PDF by ranges or individual pages</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Open Split PDF", key="go_split", use_container_width=True):
            st.session_state.page = "Split PDF"
            st.rerun()

    with col3:
        st.markdown(
            """
            <div class="tool-card card-purple">
                <div class="tool-icon">📝</div>
                <div class="tool-title">Edit PDF</div>
                <div class="tool-desc">Remove pages or insert pages from another PDF</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Open Edit PDF", key="go_edit", use_container_width=True):
            st.session_state.page = "Edit PDF"
            st.rerun()

    with col4:
        st.markdown(
            """
            <div class="tool-card card-green">
                <div class="tool-icon">📃</div>
                <div class="tool-title">PDF to Word</div>
                <div class="tool-desc">Convert PDF to editable Word document</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Open PDF to Word", key="go_convert", use_container_width=True):
            st.session_state.page = "PDF to Word"
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        '<div class="info-box">💡 Select any tool above or use the sidebar to get started.</div>',
        unsafe_allow_html=True,
    )


# ── MERGE PAGE ────────────────────────────────────────────────────────────────

def page_merge() -> None:
    section_header("🔀", "Merge PDF", "Combine multiple PDF files into one document")

    step(1, "Upload PDF files (you can upload multiple)")
    uploaded = st.file_uploader(
        "Choose PDF files",
        type=["pdf"],
        accept_multiple_files=True,
        key="merge_uploader",
        label_visibility="collapsed",
    )

    if not uploaded:
        st.info("Upload two or more PDF files to get started.")
        return

    # Build file list in session state for reordering
    upload_identity = [(f.name, f.size) for f in uploaded]
    if "merge_files" not in st.session_state or st.session_state.get(
        "merge_upload_identity"
    ) != upload_identity:
        st.session_state.merge_files = [
            {"name": f.name, "bytes": f.read(), "idx": i}
            for i, f in enumerate(uploaded)
        ]
        st.session_state.merge_upload_identity = upload_identity

    files = st.session_state.merge_files

    step(2, "Arrange files in merge order")

    for pos, item in enumerate(files):
        cols = st.columns([6, 1, 1, 1])
        try:
            page_count = get_page_count(item["bytes"])
            desc = f'{page_count} pages · {fmt_size(len(item["bytes"]))}'
        except Exception:
            desc = '<span style="color:#c0392b;">not a valid PDF — will be skipped</span>'
        with cols[0]:
            st.markdown(
                f'<div class="file-pill">📄 <strong>{item["name"]}</strong>'
                f'&nbsp;&nbsp;<span style="color:#888;font-size:0.82rem;">'
                f'{desc}</span></div>',
                unsafe_allow_html=True,
            )
        with cols[1]:
            if pos > 0 and st.button("▲", key=f"up_{pos}", help="Move up"):
                files[pos - 1], files[pos] = files[pos], files[pos - 1]
                st.rerun()
        with cols[2]:
            if pos < len(files) - 1 and st.button("▼", key=f"dn_{pos}", help="Move down"):
                files[pos], files[pos + 1] = files[pos + 1], files[pos]
                st.rerun()
        with cols[3]:
            if st.button("✕", key=f"rm_{pos}", help="Remove"):
                files.pop(pos)
                st.rerun()

    if len(files) < 2:
        st.warning("Add at least 2 files to merge.")
        return

    st.markdown("<br>", unsafe_allow_html=True)
    step(3, "Merge and download")

    if st.button("🔀 Merge PDFs", type="primary", use_container_width=True):
        with st.spinner("Merging PDFs…"):
            try:
                result = merge_pdfs([f["bytes"] for f in files])
                st.session_state.merge_result = result
                total_pages = get_page_count(result)
                st.success(
                    f"Merged {len(files)} files → {total_pages} pages "
                    f"({fmt_size(len(result))})"
                )
            except Exception as e:
                st.error(f"Merge failed: {e}")

    if "merge_result" in st.session_state:
        result = st.session_state.merge_result
        col_dl, col_preview = st.columns([1, 2])
        with col_dl:
            st.download_button(
                "⬇️ Download Merged PDF",
                data=result,
                file_name="merged.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        with col_preview:
            with st.expander("Preview merged PDF", expanded=False):
                st.markdown(pdf_preview_html(result, 400), unsafe_allow_html=True)


# ── SPLIT PAGE ────────────────────────────────────────────────────────────────

def page_split() -> None:
    section_header("✂️", "Split PDF", "Split a PDF into multiple parts or individual pages")

    step(1, "Upload a PDF file")
    uploaded = st.file_uploader(
        "Choose a PDF", type=["pdf"], key="split_uploader", label_visibility="collapsed"
    )
    if not uploaded:
        st.info("Upload a PDF file to get started.")
        return

    pdf_bytes, total_pages = read_pdf_or_error(uploaded)
    if pdf_bytes is None:
        return
    st.markdown(
        f'<div class="info-box">📄 <strong>{uploaded.name}</strong> — '
        f'{total_pages} pages · {fmt_size(len(pdf_bytes))}</div>',
        unsafe_allow_html=True,
    )

    step(2, "Choose split method")
    method = st.radio(
        "Split method",
        [
            "Split by page ranges",
            "Split every N pages",
            "Extract specific pages",
            "Split into individual pages",
        ],
        label_visibility="collapsed",
        horizontal=False,
    )

    split_config: dict = {"method": method}

    if method == "Split by page ranges":
        st.markdown(
            '<div class="info-box">Enter comma-separated ranges, e.g. <code>1-3, 4-7, 8-10</code>. '
            "Each range becomes a separate PDF file.</div>",
            unsafe_allow_html=True,
        )
        range_str = st.text_input(
            "Page ranges", placeholder=f"e.g. 1-3, 4-{total_pages}", key="split_ranges"
        )
        split_config["range_str"] = range_str

    elif method == "Split every N pages":
        n = st.number_input(
            "Pages per part",
            min_value=1,
            max_value=max(1, total_pages - 1),
            value=min(5, max(1, total_pages - 1)),
            step=1,
            key="split_n",
        )
        split_config["n"] = int(n)

    elif method == "Extract specific pages":
        st.markdown(
            '<div class="info-box">Enter pages/ranges to keep, e.g. <code>1, 3, 5-7</code>. '
            "All specified pages are combined into one PDF.</div>",
            unsafe_allow_html=True,
        )
        page_str = st.text_input(
            "Pages to extract", placeholder="e.g. 1, 3, 5-7", key="split_pages"
        )
        split_config["page_str"] = page_str

    else:  # Split into individual pages
        st.markdown(
            f'<div class="info-box">Will create {total_pages} separate PDF files '
            "packed into a ZIP archive.</div>",
            unsafe_allow_html=True,
        )

    step(3, "Split and download")
    if st.button("✂️ Split PDF", type="primary", use_container_width=True):
        with st.spinner("Splitting PDF…"):
            try:
                m = split_config["method"]
                if m == "Split by page ranges":
                    rs = split_config.get("range_str", "").strip()
                    if not rs:
                        st.error("Please enter page ranges.")
                        return
                    parts = split_by_ranges(pdf_bytes, rs)

                elif m == "Split every N pages":
                    parts = split_every_n_pages(pdf_bytes, split_config["n"])

                elif m == "Extract specific pages":
                    ps = split_config.get("page_str", "").strip()
                    if not ps:
                        st.error("Please enter pages to extract.")
                        return
                    extracted = extract_pages(pdf_bytes, ps)
                    parts = {"extracted_pages.pdf": extracted}

                else:  # individual
                    parts = split_all_pages(pdf_bytes)

                st.session_state.split_result = parts
                st.success(f"Created {len(parts)} file(s).")
            except ValueError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Split failed: {e}")

    if "split_result" in st.session_state:
        parts = st.session_state.split_result
        if len(parts) == 1:
            name, data = next(iter(parts.items()))
            st.download_button(
                f"⬇️ Download {name}",
                data=data,
                file_name=name,
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            zip_bytes = pack_zip(parts)
            st.download_button(
                f"⬇️ Download All ({len(parts)} files as ZIP)",
                data=zip_bytes,
                file_name="split_pages.zip",
                mime="application/zip",
                use_container_width=True,
            )
            with st.expander(f"Files in ZIP ({len(parts)} files)", expanded=False):
                for name in sorted(parts.keys()):
                    pages = get_page_count(parts[name])
                    st.text(f"  📄 {name}  ({pages} page{'s' if pages!=1 else ''})")


# ── EDIT PAGE ─────────────────────────────────────────────────────────────────

def page_edit() -> None:
    section_header(
        "📝",
        "Edit PDF",
        "Remove unwanted pages or insert pages from another PDF",
    )

    step(1, "Upload your base PDF")
    uploaded = st.file_uploader(
        "Base PDF", type=["pdf"], key="edit_uploader", label_visibility="collapsed"
    )
    if not uploaded:
        st.info("Upload a PDF to edit.")
        return

    pdf_bytes, total_pages = read_pdf_or_error(uploaded)
    if pdf_bytes is None:
        return
    st.markdown(
        f'<div class="info-box">📄 <strong>{uploaded.name}</strong> — '
        f'{total_pages} pages · {fmt_size(len(pdf_bytes))}</div>',
        unsafe_allow_html=True,
    )

    tab_remove, tab_insert, tab_reorder = st.tabs(
        ["🗑️ Remove Pages", "➕ Insert Pages", "🔃 Reorder Pages"]
    )

    # ── TAB: REMOVE ──────────────────────────────────────────────────────────
    with tab_remove:
        step(2, "Select pages to remove")
        st.markdown(
            '<div class="info-box">Check the pages you want to <strong>remove</strong> '
            "from the PDF. The rest will be kept.</div>",
            unsafe_allow_html=True,
        )

        cols_per_row = 10
        rows = (total_pages + cols_per_row - 1) // cols_per_row
        remove_pages_sel: list[int] = []

        for r in range(rows):
            cols = st.columns(cols_per_row)
            for c in range(cols_per_row):
                page_idx = r * cols_per_row + c
                if page_idx >= total_pages:
                    break
                with cols[c]:
                    if st.checkbox(
                        f"P{page_idx+1}", key=f"rm_pg_{page_idx}", value=False
                    ):
                        remove_pages_sel.append(page_idx)

        if remove_pages_sel:
            st.warning(
                f"Will remove {len(remove_pages_sel)} page(s): "
                + ", ".join(str(p + 1) for p in remove_pages_sel)
                + f". {total_pages - len(remove_pages_sel)} page(s) remaining."
            )

        if st.button(
            "🗑️ Remove Selected Pages",
            type="primary",
            use_container_width=True,
            key="btn_remove",
        ):
            if not remove_pages_sel:
                st.error("No pages selected to remove.")
            else:
                with st.spinner("Removing pages…"):
                    try:
                        result = remove_pages(pdf_bytes, remove_pages_sel)
                        st.session_state.edit_result = result
                        st.session_state.edit_result_name = (
                            f"edited_{uploaded.name}"
                        )
                        remaining = get_page_count(result)
                        st.success(
                            f"Removed {len(remove_pages_sel)} page(s). "
                            f"{remaining} page(s) remaining."
                        )
                    except ValueError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Operation failed: {e}")

    # ── TAB: INSERT ──────────────────────────────────────────────────────────
    with tab_insert:
        step(2, "Upload the PDF to insert")
        insert_file = st.file_uploader(
            "PDF to insert",
            type=["pdf"],
            key="insert_uploader",
            label_visibility="collapsed",
        )
        if insert_file:
            insert_bytes, insert_pages_count = read_pdf_or_error(insert_file)
        else:
            insert_bytes = None

        if insert_bytes is not None:
            st.markdown(
                f'<div class="info-box">📄 <strong>{insert_file.name}</strong> — '
                f"{insert_pages_count} pages</div>",
                unsafe_allow_html=True,
            )

            step(3, "Choose insertion position")
            position_label = st.selectbox(
                "Insert after page",
                options=["Beginning (before page 1)"]
                + [f"After page {i}" for i in range(1, total_pages + 1)],
                key="insert_pos",
            )
            if position_label == "Beginning (before page 1)":
                position = 0
            else:
                position = int(position_label.split("page ")[1])

            if st.button(
                "➕ Insert Pages",
                type="primary",
                use_container_width=True,
                key="btn_insert",
            ):
                with st.spinner("Inserting pages…"):
                    try:
                        result = insert_pdf(pdf_bytes, insert_bytes, position)
                        st.session_state.edit_result = result
                        st.session_state.edit_result_name = (
                            f"edited_{uploaded.name}"
                        )
                        new_total = get_page_count(result)
                        st.success(
                            f"Inserted {insert_pages_count} page(s) at position {position}. "
                            f"New total: {new_total} pages."
                        )
                    except Exception as e:
                        st.error(f"Insert failed: {e}")
        elif not insert_file:
            st.info("Upload a PDF whose pages you want to insert into the base PDF.")

    # ── TAB: REORDER ─────────────────────────────────────────────────────────
    with tab_reorder:
        step(2, "Specify new page order")
        st.markdown(
            '<div class="info-box">Enter the new page order as comma-separated page numbers, '
            f"e.g. <code>3, 1, 2, 4-{total_pages}</code>. "
            "You can also use ranges. Every page must appear exactly once.</div>",
            unsafe_allow_html=True,
        )
        order_str = st.text_input(
            "New page order",
            value=", ".join(str(i) for i in range(1, total_pages + 1)),
            key="reorder_str",
        )

        if st.button(
            "🔃 Apply New Order",
            type="primary",
            use_container_width=True,
            key="btn_reorder",
        ):
            with st.spinner("Reordering pages…"):
                try:
                    from tools.splitter import _parse_range_string

                    groups = _parse_range_string(order_str, total_pages)
                    new_order = []
                    for g in groups:
                        new_order.extend(g)

                    if sorted(new_order) != list(range(total_pages)):
                        st.error(
                            "Each page must appear exactly once. "
                            f"Expected pages 1–{total_pages}."
                        )
                    else:
                        result = reorder_pages(pdf_bytes, new_order)
                        st.session_state.edit_result = result
                        st.session_state.edit_result_name = (
                            f"reordered_{uploaded.name}"
                        )
                        st.success(f"Pages reordered successfully ({total_pages} pages).")
                except ValueError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Reorder failed: {e}")

    # ── Download result ───────────────────────────────────────────────────────
    if "edit_result" in st.session_state:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("---")
        result = st.session_state.edit_result
        result_name = st.session_state.get("edit_result_name", "edited.pdf")

        col_dl, col_preview = st.columns([1, 2])
        with col_dl:
            pages_out = get_page_count(result)
            st.markdown(
                f'<div class="info-box">✅ Result: {pages_out} pages · {fmt_size(len(result))}</div>',
                unsafe_allow_html=True,
            )
            st.download_button(
                "⬇️ Download Edited PDF",
                data=result,
                file_name=result_name,
                mime="application/pdf",
                use_container_width=True,
            )
        with col_preview:
            with st.expander("Preview result", expanded=False):
                st.markdown(pdf_preview_html(result, 400), unsafe_allow_html=True)


# ── CONVERT PAGE ──────────────────────────────────────────────────────────────

def page_convert() -> None:
    section_header(
        "📃",
        "PDF to Word",
        "Convert a PDF document into an editable Microsoft Word (.docx) file",
    )

    st.markdown(
        '<div class="info-box">💡 For best results use text-based PDFs. '
        "Scanned/image PDFs may have limited accuracy.</div>",
        unsafe_allow_html=True,
    )

    step(1, "Upload a PDF file")
    uploaded = st.file_uploader(
        "PDF to convert",
        type=["pdf"],
        key="conv_uploader",
        label_visibility="collapsed",
    )
    if not uploaded:
        st.info("Upload a PDF to convert it to Word.")
        return

    pdf_bytes, total_pages = read_pdf_or_error(uploaded)
    if pdf_bytes is None:
        return
    st.markdown(
        f'<div class="info-box">📄 <strong>{uploaded.name}</strong> — '
        f'{total_pages} pages · {fmt_size(len(pdf_bytes))}</div>',
        unsafe_allow_html=True,
    )

    step(2, "Convert to Word")
    if st.button("📃 Convert to Word", type="primary", use_container_width=True):
        with st.spinner(f"Converting {total_pages} page(s) to Word… this may take a moment."):
            try:
                docx_bytes = pdf_to_word(pdf_bytes)
                st.session_state.conv_result = docx_bytes
                st.session_state.conv_name = (
                    os.path.splitext(uploaded.name)[0] + ".docx"
                )
                st.success(
                    f"Converted successfully! "
                    f"Word document: {fmt_size(len(docx_bytes))}"
                )
            except ImportError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Conversion failed: {e}")

    if "conv_result" in st.session_state:
        st.download_button(
            "⬇️ Download Word Document",
            data=st.session_state.conv_result,
            file_name=st.session_state.conv_name,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True,
        )


# ── SIDEBAR + ROUTING ─────────────────────────────────────────────────────────

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
            active = st.session_state.page == page_key
            btn_style = (
                "background:#3a86ff;color:white;border-radius:8px;"
                if active
                else ""
            )
            if st.button(
                label,
                key=f"nav_{page_key}",
                use_container_width=True,
            ):
                st.session_state.page = page_key
                # Clear previous results when navigating away
                for key in [
                    "merge_result", "split_result", "edit_result", "conv_result"
                ]:
                    st.session_state.pop(key, None)
                st.rerun()

        st.markdown("---")
        st.markdown(
            "<p style='color:#666;font-size:0.75rem;text-align:center;'>"
            "Built with Streamlit + pypdf</p>",
            unsafe_allow_html=True,
        )


def main() -> None:
    build_sidebar()

    page = st.session_state.page
    if page == "Home":
        page_home()
    elif page == "Merge PDF":
        page_merge()
    elif page == "Split PDF":
        page_split()
    elif page == "Edit PDF":
        page_edit()
    elif page == "PDF to Word":
        page_convert()
    else:
        page_home()


if __name__ == "__main__":
    main()
