"""
JE Audit Analytics Tool — Main Streamlit Application
=====================================================
Run:  streamlit run app.py
Reqs: streamlit pandas numpy plotly scipy openpyxl xlsxwriter xlrd scikit-learn
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import io
import json
import math
import pickle
import random
from datetime import datetime, date, timedelta

# Path used to persist loaded data across server restarts / code changes
_SESSION_CACHE = os.path.join(os.path.dirname(__file__), ".je_session_cache.pkl")

import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from utils.data_loader        import (
    load_single_file, load_multiple_files, get_file_stats,
    load_excel_sheet, select_sheet,
    load_file_bytes, select_sheet_from_bytes,
)
from utils.column_mapper      import REQUIRED_FIELDS, auto_map_columns, get_safe_series, get_safe_numeric, safe_str
from utils.risk_scoring       import calculate_transaction_risk, rank_users, RISK_SCORES, RISK_COLORS
from utils.export_utils       import to_excel_report
from utils.doc_type_classifier import (
    load_doc_type_master, build_category_map, default_type_df,
    DEFAULT_DOC_TYPES, CATEGORY_LABELS,
)
from utils.vendor_master import (
    load_vendor_master, normalise_vendor_master, vendor_master_summary,
    find_duplicate_vendors, get_msme_vendors, get_inactive_vendors,
    get_vendor_created_dates, get_auto_column_mapping,
)
from tests.amount_tests    import run_amount_tests, run_amount_rowlevel, run_amount_aggregation
from tests.timing_tests    import run_timing_tests, run_timing_rowlevel, run_timing_aggregation
from tests.user_tests      import run_user_tests,   run_user_rowlevel,   run_user_aggregation
from tests.vendor_tests    import run_vendor_tests
from tests.benford_test    import run_benford_test


# ══════════════════════════════════════════════════════════════════════════════
# CACHED FILE LOADER  (prevents re-reading on every Streamlit rerun)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False)
def _cached_load(file_bytes: bytes, fname: str, sheet_name=0) -> pd.DataFrame:
    """Load a DataFrame from bytes — cached by content hash so the file is
    only parsed once per unique upload, regardless of how many times the page
    reruns."""
    return load_file_bytes(file_bytes, fname, sheet_name=sheet_name)


@st.cache_data(show_spinner=False)
def _cached_sheets(file_bytes: bytes, fname: str):
    return select_sheet_from_bytes(file_bytes, fname)


# ══════════════════════════════════════════════════════════════════════════════
# SESSION PERSISTENCE  (survives server restarts / hot-reloads)
# ══════════════════════════════════════════════════════════════════════════════

def _save_session_cache(df: pd.DataFrame, col_map: dict) -> None:
    """Persist df + col_map to disk so a server restart doesn't lose the data."""
    try:
        with open(_SESSION_CACHE, "wb") as f:
            pickle.dump({"df": df, "col_map": col_map}, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass  # non-fatal


def _load_session_cache() -> tuple[pd.DataFrame | None, dict]:
    """Return (df, col_map) from disk cache, or (None, {}) if unavailable."""
    try:
        if os.path.exists(_SESSION_CACHE):
            with open(_SESSION_CACHE, "rb") as f:
                data = pickle.load(f)
            return data.get("df"), data.get("col_map", {})
    except Exception:
        pass
    return None, {}


def _clear_session_cache() -> None:
    try:
        if os.path.exists(_SESSION_CACHE):
            os.remove(_SESSION_CACHE)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="JE Audit Analytics Tool",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
* { font-family: 'Inter', sans-serif; }
.stApp { background: #f4f6fa; }

.hero {
    background: linear-gradient(135deg, #1e3a5f 0%, #2d6a9f 55%, #1a9e8f 100%);
    padding: 2rem 2.5rem; border-radius: 16px; margin-bottom: 1.5rem; color: white;
}
.hero h1 { font-size: 1.8rem; font-weight: 700; margin: 0; }
.hero p  { font-size: 0.95rem; opacity: 0.88; margin: 0.4rem 0 0; }

.section-hdr {
    background: white; border-radius: 8px; padding: 0.65rem 1.2rem;
    margin: 1.2rem 0 0.8rem; font-weight: 600; font-size: 1rem;
    color: #1e3a5f; border-left: 4px solid #2d6a9f;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.kpi-card {
    background: white; border-radius: 12px; padding: 1.1rem 1.4rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08); text-align: center;
    border-top: 4px solid #2d6a9f;
}
.kpi-card.red    { border-top-color: #e74c3c; }
.kpi-card.amber  { border-top-color: #f39c12; }
.kpi-card.green  { border-top-color: #27ae60; }
.kpi-card.purple { border-top-color: #8e44ad; }
.kpi-val { font-size: 2rem; font-weight: 700; color: #1e3a5f; }
.kpi-lbl { font-size: 0.72rem; color: #6b7280; text-transform: uppercase;
           letter-spacing: 0.06em; margin-top: 0.2rem; }

.risk-critical { background:#FF3B3B; color:white; border-radius:4px;
                 padding:2px 8px; font-weight:600; font-size:0.75rem; }
.risk-high     { background:#FF8C00; color:white; border-radius:4px;
                 padding:2px 8px; font-weight:600; font-size:0.75rem; }
.risk-medium   { background:#F5A623; color:white; border-radius:4px;
                 padding:2px 8px; font-weight:600; font-size:0.75rem; }
.risk-low      { background:#4CAF50; color:white; border-radius:4px;
                 padding:2px 8px; font-weight:600; font-size:0.75rem; }

div[data-testid="stExpander"] {
    background: white; border-radius: 10px; border: 1px solid #e2e8f0;
    margin-bottom: 0.6rem;
}
.upload-hint {
    background: white; border: 2px dashed #cbd5e1; border-radius: 12px;
    padding: 2.5rem; text-align: center;
}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SESSION STATE
# ══════════════════════════════════════════════════════════════════════════════

def _init_state():
    defaults = {
        "df":           None,
        "col_map":      {},
        "params":       {},
        "exceptions_df":None,
        "benford_df":   None,
        "chi2_df":      None,
        "tx_risk_df":   None,
        "user_risk_df": None,
        "run_done":     False,
        "doc_type_map":       build_category_map(),   # default SAP classifications
        "vendor_master_df":   None,
        "vendor_master_raw":  None,                 # un-normalised, for re-mapping
        "vm_auto_col_map":   {},                   # auto-detected mapping (pre-fills panel)
        "vm_col_map":         {},                   # user-override mapping for vendor master
        "related_party_list": [],
        "custom_holidays":    {},                   # uploaded holiday {date: name} dict
        "gl_groups":          [],                   # list of {group, subgroup, match_type, value, value_end}
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR NAVIGATION
# ══════════════════════════════════════════════════════════════════════════════

_PAGES = [
    "📁  Upload Data",
    "🔗  Column Mapping",
    "🗂️  GL Account Groups",
    "⚙️   Audit Parameters",
    "🚀  Run JE Tests",
    "📋  Exception Results",
    "🔄  Data Explorer",
    "📊  Dashboard",
    "⬇️   Export Results",
]

with st.sidebar:
    st.markdown("## 🔍 JE Audit Tool")
    st.caption("B&Co Analytics")
    st.divider()

    page = st.radio("Navigation", _PAGES, index=0, label_visibility="collapsed")

    st.divider()
    # Quick dataset status
    df = st.session_state.get("df")
    if df is not None:
        st.success(f"✅ Dataset loaded: **{len(df):,}** rows")
        _exc_df = st.session_state.get("exceptions_df")
        n_exc = len(_exc_df) if _exc_df is not None else 0
        if n_exc:
            st.warning(f"⚠️ **{n_exc:,}** exceptions found")
    else:
        st.info("No dataset loaded yet")

    st.divider()
    st.caption("All processing is local — no data is transmitted.")


# ══════════════════════════════════════════════════════════════════════════════
# HERO BANNER
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="hero">
  <h1>🔍 Journal Entry Testing Engine</h1>
  <p>Statutory Audit · Forensic Analytics · GL Dump Analyser · Reusable for Any Client Dataset</p>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _section(title: str):
    st.markdown(f'<div class="section-hdr">{title}</div>', unsafe_allow_html=True)


def _kpi(label: str, value, color: str = "blue"):
    return (
        f'<div class="kpi-card {color}">'
        f'<div class="kpi-val">{value}</div>'
        f'<div class="kpi-lbl">{label}</div>'
        f'</div>'
    )


def _require_df():
    if st.session_state.df is None:
        st.warning("⚠️ Please upload a dataset first (step 1).")
        st.stop()


def _require_run():
    if not st.session_state.run_done:
        st.warning("⚠️ Please run the exception tests first (step 4).")
        st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — UPLOAD DATA
# ══════════════════════════════════════════════════════════════════════════════

def page_upload():
    _section("📁 Step 1 — Upload GL / Journal Entry Data")

    # ── Restore from disk cache (survives hot-reloads / server restarts) ───────
    if st.session_state.df is None and os.path.exists(_SESSION_CACHE):
        _cached_df, _cached_col_map = _load_session_cache()
        if _cached_df is not None:
            _mtime = datetime.fromtimestamp(os.path.getmtime(_SESSION_CACHE)).strftime("%d %b %Y %H:%M")
            st.info(
                f"**Cached dataset found** ({len(_cached_df):,} rows · saved {_mtime}). "
                "Restore it so you don't need to re-upload after a code change.",
                icon="💾",
            )
            _rc1, _rc2 = st.columns(2)
            if _rc1.button("♻️ Restore Last Dataset", type="primary", use_container_width=True):
                st.session_state.df      = _cached_df
                st.session_state.col_map = _cached_col_map
                st.session_state.run_done= False
                st.success(f"✅ Restored {len(_cached_df):,} rows from disk cache.")
                st.rerun()
            if _rc2.button("🗑️ Clear Cached Data", use_container_width=True):
                _clear_session_cache()
                st.success("Cache cleared.")
                st.rerun()
            st.divider()

    tab1, tab2 = st.tabs(["Single File", "Multiple Files / Merge"])

    with tab1:
        up = st.file_uploader(
            "Upload Excel (.xlsx / .xls) or CSV",
            type=["xlsx", "xls", "csv"],
            help="Export your GL dump from SAP / Oracle / Tally / any ERP",
        )
        if up:
            try:
                _up_bytes = up.read()
                # Sheet selection (cached — no extra read cost)
                if up.name.lower().endswith((".xlsx", ".xls")):
                    sheets, _ = _cached_sheets(_up_bytes, up.name)
                    if len(sheets) > 1:
                        sheet = st.selectbox("Select worksheet", sheets)
                    else:
                        sheet = sheets[0] if sheets else 0
                else:
                    sheet = 0

                with st.spinner("Reading file…"):
                    df_new = _cached_load(_up_bytes, up.name, sheet_name=sheet)

                df_new["_source_file"] = up.name
                st.session_state.df      = df_new
                st.session_state.col_map = auto_map_columns(df_new.columns.tolist())
                st.session_state.run_done= False
                _save_session_cache(df_new, st.session_state.col_map)
                stats = get_file_stats(df_new)
                _show_stats(stats, [up.name])
                _preview(df_new)
            except Exception as e:
                st.error(f"Error reading file: {e}")

    with tab2:
        ups = st.file_uploader(
            "Upload multiple files (will be merged row-wise)",
            type=["xlsx", "xls", "csv"],
            accept_multiple_files=True,
        )
        if ups:
            with st.spinner(f"Loading and merging {len(ups)} file(s)…"):
                try:
                    _dfs, _errs = [], []
                    for _f in ups:
                        try:
                            _fb = _f.read()
                            _df = _cached_load(_fb, _f.name)
                            _df["_source_file"] = _f.name
                            _dfs.append(_df)
                        except Exception as _ex:
                            _errs.append(f"{_f.name}: {_ex}")
                    if not _dfs:
                        raise ValueError("No files could be loaded. Check file formats and try again.")
                    df_new = pd.concat(_dfs, ignore_index=True)
                    for err in _errs:
                        st.warning(f"⚠️ {err}")
                    st.session_state.df      = df_new
                    st.session_state.col_map = auto_map_columns(df_new.columns.tolist())
                    st.session_state.run_done= False
                    _save_session_cache(df_new, st.session_state.col_map)
                    stats = get_file_stats(df_new, [f.name for f in ups])
                    _show_stats(stats, [f.name for f in ups])
                    _preview(df_new)
                except Exception as e:
                    st.error(f"Error merging files: {e}")

            # ── Download — outside spinner so it persists on reruns ────────────
            if st.session_state.df is not None:
                _m_df  = st.session_state.df
                _m_ts  = datetime.now().strftime("%Y%m%d_%H%M")
                st.markdown("##### 📥 Download Consolidated File")
                _m1, _m2 = st.columns(2)

                # CSV — chunked write to avoid MemoryError on large datasets
                _m_csv = _df_to_csv_bytes(_m_df)
                _m1.download_button(
                    label="⬇️ Download as CSV",
                    data=_m_csv,
                    file_name=f"Consolidated_{_m_ts}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="merge_dl_csv",
                )

                # Excel — only for datasets that won't exceed Excel's row limit
                _EXCEL_ROW_LIMIT = 1_048_575
                if len(_m_df) <= _EXCEL_ROW_LIMIT:
                    _m_xl = io.BytesIO()
                    with pd.ExcelWriter(_m_xl, engine="xlsxwriter") as _wr:
                        _m_df.to_excel(_wr, index=False, sheet_name="Consolidated")
                        _wb = _wr.book
                        _ws = _wr.sheets["Consolidated"]
                        _hf = _wb.add_format({"bold": True, "bg_color": "#1F3864", "font_color": "white"})
                        for _ci, _cn in enumerate(_m_df.columns):
                            _ws.write(0, _ci, str(_cn), _hf)
                            _ws.set_column(_ci, _ci, max(14, len(str(_cn)) + 2))
                    _m_xl.seek(0)
                    _m2.download_button(
                        label="⬇️ Download as Excel",
                        data=_m_xl,
                        file_name=f"Consolidated_{_m_ts}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                        key="merge_dl_xlsx",
                    )
                else:
                    _m2.info(
                        f"Excel not available — {len(_m_df):,} rows exceeds Excel's limit of 1,048,575. Use CSV.",
                        icon="ℹ️",
                    )

    # ── Document Type Master ──────────────────────────────────────────────────
    st.markdown("---")
    _section("📋 Document Type Master (Optional)")

    _dc1, _dc2 = st.columns([3, 1])
    with _dc1:
        st.info(
            "Upload your company's document type classification file. "
            "Required columns: **Type** (doc type code) and **Category** "
            f"(one of: {', '.join(CATEGORY_LABELS)}). "
            "Unrecognised types fall back to SAP defaults.",
            icon="ℹ️",
        )
    with _dc2:
        _tmpl_df  = default_type_df()
        _tmpl_buf = io.BytesIO()
        with pd.ExcelWriter(_tmpl_buf, engine="xlsxwriter") as _wr:
            _tmpl_df.to_excel(_wr, index=False, sheet_name="Doc Types")
        _tmpl_buf.seek(0)
        st.download_button(
            "⬇️ Download Default Template",
            data=_tmpl_buf,
            file_name="doc_type_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="Download the built-in SAP classification to edit and re-upload",
        )

    dt_file = st.file_uploader(
        "Upload Document Type Master (CSV / Excel)",
        type=["csv", "xlsx", "xls"],
        key="dt_upload",
        help="Must contain at minimum: Type and Category columns",
    )
    if dt_file:
        try:
            dt_df = load_doc_type_master(dt_file)
            new_map = build_category_map(dt_df)
            st.session_state.doc_type_map = new_map
            st.success(
                f"Loaded {len(dt_df)} document type definitions "
                f"({len(new_map)} active entries after merging with defaults)."
            )
        except Exception as _dte:
            st.error(f"Error reading document type file: {_dte}")

    # Preview current classification
    with st.expander("📊 Current Document Type Classification", expanded=False):
        _cur_map = st.session_state.get("doc_type_map", {})
        if _cur_map:
            _prev_rows = [
                {
                    "Type":        code.upper(),
                    "Description": DEFAULT_DOC_TYPES.get(code.upper(), ("",))[0],
                    "Category":    cat,
                }
                for code, cat in sorted(_cur_map.items())
            ]
            st.dataframe(pd.DataFrame(_prev_rows), use_container_width=True, height=320)

    # ── Holiday / Non-Working Day List ───────────────────────────────────────
    st.markdown("---")
    _section("📅 Holiday & Non-Working Day List (Optional)")

    _hl_c1, _hl_c2 = st.columns([3, 1])
    with _hl_c1:
        st.info(
            "Upload a list of public holidays. The tool already auto-detects **Sundays**. "
            "Upload your company's public holiday calendar to flag entries posted on those days. "
            "File must have at least one column with dates. "
            "Optional second column for holiday name/description.",
            icon="ℹ️",
        )
    with _hl_c2:
        _hl_tmpl = pd.DataFrame([
            {"Date": "2024-04-14", "Holiday": "Dr. Ambedkar Jayanti"},
            {"Date": "2024-08-15", "Holiday": "Independence Day"},
            {"Date": "2024-10-02", "Holiday": "Gandhi Jayanti"},
            {"Date": "2024-11-01", "Holiday": "Diwali"},
            {"Date": "2025-01-26", "Holiday": "Republic Day"},
        ])
        _hl_buf = io.BytesIO()
        with pd.ExcelWriter(_hl_buf, engine="xlsxwriter") as _wr:
            _hl_tmpl.to_excel(_wr, index=False, sheet_name="Holidays")
        _hl_buf.seek(0)
        st.download_button(
            "⬇️ Download Template",
            data=_hl_buf,
            file_name="holiday_list_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    hl_file = st.file_uploader(
        "Upload Holiday List (CSV / Excel)",
        type=["csv", "xlsx", "xls"],
        key="hl_upload",
    )
    if hl_file:
        try:
            _hl_bytes = hl_file.read()
            _hl_name  = hl_file.name.lower()
            if _hl_name.endswith((".xlsx", ".xls")):
                _hl_df = pd.read_excel(io.BytesIO(_hl_bytes), dtype=str)
            else:
                try:
                    _hl_df = pd.read_csv(io.BytesIO(_hl_bytes), dtype=str, encoding="utf-8-sig")
                except UnicodeDecodeError:
                    _hl_df = pd.read_csv(io.BytesIO(_hl_bytes), dtype=str, encoding="latin-1")

            # ── Robust date detection: try every column, every format ─────────
            _DATE_FMTS = [
                "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
                "%Y-%m-%d", "%Y/%m/%d",
                "%d-%b-%Y", "%d %b %Y", "%d-%b-%y",
                "%m/%d/%Y", "%m-%d-%Y",
                "%d-%m-%y", "%d/%m/%y",
            ]

            def _try_parse_col(series: pd.Series) -> pd.Series:
                best: pd.Series = pd.to_datetime(series, errors="coerce", dayfirst=True)
                for fmt in _DATE_FMTS:
                    attempt = pd.to_datetime(series, errors="coerce", format=fmt)
                    if attempt.notna().sum() > best.notna().sum():
                        best = attempt
                return best

            # Find best date column
            _best_col, _best_series, _best_count = None, None, 0
            for _col in _hl_df.columns:
                _s = _try_parse_col(_hl_df[_col].astype(str).str.strip())
                _n = _s.notna().sum()
                if _n > _best_count:
                    _best_count, _best_col, _best_series = _n, _col, _s

            # Find best name column: prefer columns whose header hints at a name
            _NAME_HINTS = ["holiday", "name", "occasion", "description",
                           "festival", "event", "remark", "day", "title"]
            _name_col = None
            _remaining = [c for c in _hl_df.columns if c != _best_col]
            for _hint in _NAME_HINTS:
                for _c in _remaining:
                    if _hint in str(_c).lower():
                        _name_col = _c
                        break
                if _name_col:
                    break
            # Fall back to first non-date column if no hint matched
            if _name_col is None and _remaining:
                _name_col = _remaining[0]

            _min_required = max(1, len(_hl_df) * 0.3)
            if _best_series is not None and _best_count >= _min_required:
                _valid_mask = _best_series.notna()
                _dates = _best_series[_valid_mask].dt.date.tolist()
                if _name_col:
                    _names = (
                        _hl_df.loc[_valid_mask, _name_col]
                        .fillna("").astype(str).str.strip().tolist()
                    )
                else:
                    _names = [""] * len(_dates)
                st.session_state.custom_holidays = dict(zip(_dates, _names))

                _named_count = sum(1 for n in _names if n)
                _name_info = (f" with holiday names from **'{_name_col}'**"
                              if _name_col and _named_count else "")
                st.success(
                    f"Loaded **{len(_dates)}** holiday dates from column **'{_best_col}'**{_name_info}. "
                    f"Range: {min(_dates)} → {max(_dates)}"
                )
                with st.expander("📋 Loaded Holidays", expanded=False):
                    _hol_preview = pd.DataFrame({
                        "Date": _dates,
                        "Holiday Name": _names,
                    })
                    st.dataframe(_hol_preview, use_container_width=True, height=300)
            else:
                st.error(
                    "Could not detect a date column in the uploaded file. "
                    "Accepted formats: DD-MM-YYYY, YYYY-MM-DD, DD/MM/YYYY, DD-Mon-YYYY, etc. "
                    "The date column can be in any position."
                )
        except Exception as _hle:
            st.error(f"Error reading holiday file: {_hle}")
    elif st.session_state.get("custom_holidays"):
        _ch = st.session_state.custom_holidays
        _ch_dates = list(_ch.keys()) if isinstance(_ch, dict) else _ch
        st.success(f"✅ {len(_ch_dates)} holidays already loaded.")

    # ── Vendor Master ─────────────────────────────────────────────────────────
    st.markdown("---")
    _section("🏢 Vendor Master (Optional — enables vendor analytics)")

    st.info(
        "Upload your SAP vendor master extract. Enables: duplicate vendor detection "
        "(GSTIN/PAN), MSME delayed payment check, vendor creation date analysis, "
        "and inactive/blocked vendor alerts.",
        icon="ℹ️",
    )
    vm_file = st.file_uploader(
        "Upload Vendor Master (CSV / Excel)",
        type=["csv", "xlsx", "xls"],
        key="vm_upload",
    )
    if vm_file:
        try:
            _raw_vm = load_vendor_master(vm_file)
            st.session_state.vendor_master_raw  = _raw_vm
            st.session_state.vm_auto_col_map    = get_auto_column_mapping(_raw_vm)  # pre-fill panel
            st.session_state.vm_col_map         = {}        # reset user overrides on new upload
            _vm_norm = normalise_vendor_master(_raw_vm)
            st.session_state.vendor_master_df  = _vm_norm
            _vm_stats = vendor_master_summary(_vm_norm)
            _vc1, _vc2, _vc3, _vc4 = st.columns(4)
            _vc1.markdown(_kpi("Total Vendors",    f"{_vm_stats['total']:,}",    "blue"),   unsafe_allow_html=True)
            _vc2.markdown(_kpi("MSME Vendors",     f"{_vm_stats['msme']:,}",     "amber"),  unsafe_allow_html=True)
            _vc3.markdown(_kpi("With GSTIN",       f"{_vm_stats['with_gstin']:,}","green"),  unsafe_allow_html=True)
            _vc4.markdown(_kpi("Inactive/Blocked", f"{_vm_stats['inactive']:,}", "red"),    unsafe_allow_html=True)
            with st.expander("👁️ Vendor Master Preview", expanded=False):
                st.dataframe(_vm_norm.head(100), use_container_width=True)
        except Exception as _vme:
            st.error(f"Error reading vendor master: {_vme}")
    elif st.session_state.get("vendor_master_df") is not None:
        st.success("✅ Vendor master already loaded from this session.")

    # Vendor Master Column Mapping panel (shown whenever raw data is available)
    if st.session_state.get("vendor_master_raw") is not None:
        with st.expander("🔗 Vendor Master Column Mapping", expanded=False):
            _raw_df      = st.session_state.vendor_master_raw
            _vm_cols     = ["— Not Mapped —"] + _raw_df.columns.tolist()
            _auto_map    = st.session_state.get("vm_auto_col_map", {})   # from auto-detection
            _user_map    = st.session_state.get("vm_col_map", {})        # user overrides

            # Standard fields to map
            _VM_FIELDS = {
                "vendor_code":     "Vendor Code *",
                "vendor_name":     "Vendor Name *",
                "gstin":           "GST Number *",
                "pan":             "PAN No. *",
                "created_date":    "Created Date *",
                "msme":            "MSME Status *",
                "msme_no":         "MSME No.",
                "msme_valid_from": "MSME Valid From",
                "status":          "Vendor Status *",
                "account_group":   "Account Group Name",
                "region":          "Region",
            }

            # Show detection status summary
            _detected   = [k for k in _VM_FIELDS if _auto_map.get(k) or _user_map.get(k)]
            _undetected = [_VM_FIELDS[k].rstrip(" *") for k in _VM_FIELDS
                           if not _auto_map.get(k) and not _user_map.get(k)]
            if _undetected:
                st.warning(
                    f"**{len(_undetected)} field(s) not auto-detected:** "
                    f"{', '.join(_undetected)}. Select the correct column from the dropdowns below.",
                    icon="⚠️",
                )
            else:
                st.success(f"All {len(_VM_FIELDS)} fields auto-detected. "
                           "Review below and correct any mismatches.", icon="✅")

            st.caption("🟢 Auto-detected  |  🔵 Manually set  |  🔴 Not mapped  —  (* = used in analytics)")
            st.divider()

            # Show 3 columns per row — pre-fill with auto-detected or user override
            _new_user_map: dict = {}
            _vf_items = list(_VM_FIELDS.items())
            for _row_s in range(0, len(_vf_items), 3):
                _row_cols = st.columns(3)
                for _ci, (fkey, flabel) in enumerate(_vf_items[_row_s:_row_s + 3]):
                    # Priority: user override → auto-detected → Not Mapped
                    _current = _user_map.get(fkey) or _auto_map.get(fkey) or "— Not Mapped —"
                    _default = _current if _current in _vm_cols else "— Not Mapped —"

                    # Label badge: show source
                    if _user_map.get(fkey):
                        _badge = "🔵"
                    elif _auto_map.get(fkey):
                        _badge = "🟢"
                    else:
                        _badge = "🔴"

                    _sel = _row_cols[_ci].selectbox(
                        f"{_badge} {flabel}",
                        _vm_cols,
                        index=_vm_cols.index(_default),
                        key=f"vm_map_{fkey}",
                    )
                    if _sel != "— Not Mapped —":
                        _new_user_map[fkey] = _sel

            if st.button("✅ Apply Mapping", key="vm_apply_map", type="primary"):
                try:
                    _rename   = {v: k for k, v in _new_user_map.items()}
                    _remapped = _raw_df.rename(columns=_rename)
                    _renormed = normalise_vendor_master(_remapped)
                    st.session_state.vendor_master_df = _renormed
                    st.session_state.vm_col_map       = _new_user_map
                    _s = vendor_master_summary(_renormed)
                    st.success(
                        f"✅ Mapping applied — {_s['total']:,} vendors · "
                        f"{_s['msme']:,} MSME · {_s['with_gstin']:,} with GSTIN · "
                        f"{_s['inactive']:,} inactive"
                    )
                except Exception as _mpe:
                    st.error(f"Error applying mapping: {_mpe}")

    # ── Related Party List ────────────────────────────────────────────────────
    st.markdown("---")
    _section("🔗 Related Party List (Optional)")

    _rp_c1, _rp_c2 = st.columns([3, 1])
    with _rp_c1:
        st.info(
            "Upload related party master. Columns: **Party Name**, **Vendor Code**, **Customer Code**, **Relationship**. "
            "A party may have multiple rows (one per vendor code / state GSTIN registration). "
            "Matching is done by Vendor Code, Customer Code, and Party Name substring.",
            icon="ℹ️",
        )
    with _rp_c2:
        _rp_tmpl = pd.DataFrame([
            {"Party Name": "ABC Pvt Ltd",  "Vendor Code": "V001", "Customer Code": "",    "Relationship": "Subsidiary"},
            {"Party Name": "ABC Pvt Ltd",  "Vendor Code": "V002", "Customer Code": "",    "Relationship": "Subsidiary"},
            {"Party Name": "ABC Pvt Ltd",  "Vendor Code": "",     "Customer Code": "C001","Relationship": "Subsidiary"},
            {"Party Name": "XYZ Ltd",      "Vendor Code": "V010", "Customer Code": "C010","Relationship": "Group Company"},
            {"Party Name": "John Doe",     "Vendor Code": "V020", "Customer Code": "",    "Relationship": "Director / KMP"},
        ])
        _rp_buf = io.BytesIO()
        with pd.ExcelWriter(_rp_buf, engine="xlsxwriter") as _wr:
            _rp_tmpl.to_excel(_wr, index=False, sheet_name="Related Parties")
        _rp_buf.seek(0)
        st.download_button(
            "⬇️ Download Template",
            data=_rp_buf,
            file_name="related_party_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    rp_file = st.file_uploader(
        "Upload Related Party List (CSV / Excel)",
        type=["csv", "xlsx", "xls"],
        key="rp_upload",
    )
    if rp_file:
        try:
            _rp_name = rp_file.name.lower()
            if _rp_name.endswith((".xlsx", ".xls")):
                _rp_df = pd.read_excel(rp_file, dtype=str)
            else:
                _rp_df = pd.read_csv(rp_file, dtype=str)
            # Normalise column names
            _rp_df.columns = [c.strip() for c in _rp_df.columns]
            _rp_lc = {c.lower(): c for c in _rp_df.columns}

            # Flexible column detection
            _vc_col  = next((v for k, v in _rp_lc.items() if k in ("vendor code", "vendor_code", "vendorcode", "vend. code")), None)
            _cc_col  = next((v for k, v in _rp_lc.items() if k in ("customer code", "customer_code", "customercode", "cust code")), None)
            _pn_col  = next((v for k, v in _rp_lc.items() if k in ("party name", "party_name", "name", "vendor name", "company name")), None)
            _rel_col = next((v for k, v in _rp_lc.items() if k in ("relationship", "relation", "nature", "type", "party type")), None)

            # Fallback: single "code" column → treat as vendor code
            if not _vc_col and not _cc_col:
                _vc_col = next((v for k, v in _rp_lc.items() if k in ("code", "party code")), None)

            if not _vc_col and not _cc_col and not _pn_col:
                st.error("Could not detect Party Name, Vendor Code, or Customer Code columns. "
                         "Please use the template.")
            else:
                _rp_records = []
                for _, _row in _rp_df.iterrows():
                    _vc_val  = str(_row[_vc_col]).strip().upper()  if _vc_col  else ""
                    _cc_val  = str(_row[_cc_col]).strip().upper()  if _cc_col  else ""
                    _pn_val  = str(_row[_pn_col]).strip()          if _pn_col  else ""
                    _rel_val = str(_row[_rel_col]).strip()         if _rel_col else "Related Party"
                    # Skip rows where all key fields are blank or 'nan'
                    if all(v in ("", "NAN", "NONE") for v in [_vc_val, _cc_val, _pn_val]):
                        continue
                    _rp_records.append({
                        "vendor_code":   _vc_val  if _vc_val  not in ("", "NAN", "NONE") else "",
                        "customer_code": _cc_val  if _cc_val  not in ("", "NAN", "NONE") else "",
                        "party_name":    _pn_val  if _pn_val  not in ("", "nan", "None") else "",
                        "relationship":  _rel_val if _rel_val not in ("", "nan", "None") else "Related Party",
                    })
                st.session_state.related_party_list = _rp_records
                _vc_cnt = sum(1 for r in _rp_records if r["vendor_code"])
                _cc_cnt = sum(1 for r in _rp_records if r["customer_code"])
                _pn_cnt = len({r["party_name"] for r in _rp_records if r["party_name"]})
                st.success(
                    f"Loaded {len(_rp_records)} related party records — "
                    f"{_vc_cnt} vendor codes, {_cc_cnt} customer codes, {_pn_cnt} unique party names."
                )
                with st.expander("👁️ Related Party Preview", expanded=False):
                    st.dataframe(_rp_df.head(50), use_container_width=True)
        except Exception as _rpe:
            st.error(f"Error reading related party file: {_rpe}")
    elif st.session_state.get("related_party_list"):
        _rp_loaded = st.session_state.related_party_list
        st.success(f"✅ {len(_rp_loaded)} related party records already loaded.")

    if st.session_state.df is None:
        st.markdown("""
        <div class="upload-hint">
          <h3 style="color:#1e3a5f;">📂 Upload your GL Dump to begin</h3>
          <p style="color:#6b7280;">Supports SAP, Oracle, Tally, QuickBooks exports in Excel or CSV format</p>
          <p style="color:#94a3b8;font-size:0.85rem;">
            All data is processed entirely within your local session — nothing is stored or transmitted.
          </p>
        </div>
        """, unsafe_allow_html=True)
    else:
        # ── Persistent download section (always visible when data is loaded) ────
        st.markdown("---")
        _section("📥 Download Loaded Dataset")
        _dl_df = st.session_state.df
        _dl_ts = datetime.now().strftime("%Y%m%d_%H%M")
        _dl_client = (st.session_state.get("params", {}).get("client_name") or "Dataset").replace(" ", "_")
        st.caption(f"Current dataset: **{len(_dl_df):,} rows × {len(_dl_df.columns):,} columns**")
        _pdl1, _pdl2 = st.columns(2)

        # CSV — chunked write to avoid MemoryError on large datasets
        _pdl_csv = _df_to_csv_bytes(_dl_df)
        _pdl1.download_button(
            label="⬇️ Download as CSV",
            data=_pdl_csv,
            file_name=f"JE_Data_{_dl_client}_{_dl_ts}.csv",
            mime="text/csv",
            use_container_width=True,
            key="persistent_dl_csv",
        )

        # Excel — only if within Excel row limit
        if len(_dl_df) <= 1_048_575:
            _pdl_xl = io.BytesIO()
            with pd.ExcelWriter(_pdl_xl, engine="xlsxwriter") as _wr:
                _dl_df.to_excel(_wr, index=False, sheet_name="JE Data")
                _wb2 = _wr.book
                _ws2 = _wr.sheets["JE Data"]
                _hdr2 = _wb2.add_format({"bold": True, "bg_color": "#1F3864", "font_color": "white"})
                for _ci2, _cn2 in enumerate(_dl_df.columns):
                    _ws2.write(0, _ci2, str(_cn2), _hdr2)
                    _ws2.set_column(_ci2, _ci2, max(14, len(str(_cn2)) + 2))
            _pdl_xl.seek(0)
            _pdl2.download_button(
                label="⬇️ Download as Excel",
                data=_pdl_xl,
                file_name=f"JE_Data_{_dl_client}_{_dl_ts}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="persistent_dl_xlsx",
            )
        else:
            _pdl2.info(
                f"Excel not available — {len(_dl_df):,} rows exceeds Excel's 1,048,575 row limit. Use CSV.",
                icon="ℹ️",
            )


def _df_to_csv_bytes(df: pd.DataFrame, chunksize: int = 50_000) -> io.BytesIO:
    """Stream DataFrame to CSV directly into a BytesIO buffer.

    Passing a file-like object to to_csv() makes pandas write row-by-row
    without building an intermediate in-memory string, which avoids MemoryError
    on very large DataFrames.  Using write_through=True on the TextIOWrapper
    ensures no second buffer layer accumulates between flushes.
    """
    buf = io.BytesIO()
    buf.write(b"\xef\xbb\xbf")  # UTF-8 BOM — opens cleanly in Excel
    wrapper = io.TextIOWrapper(buf, encoding="utf-8", newline="", write_through=True)
    for i, start in enumerate(range(0, len(df), chunksize)):
        chunk = df.iloc[start : start + chunksize]
        chunk.to_csv(wrapper, index=False, header=(i == 0))
        wrapper.flush()
    wrapper.detach()  # release buf without closing it
    buf.seek(0)
    return buf


def _show_stats(stats: dict, names: list):
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_kpi("Rows Loaded",    f"{stats['rows']:,}",    "blue"),   unsafe_allow_html=True)
    c2.markdown(_kpi("Columns",        stats["columns"],         "green"),  unsafe_allow_html=True)
    c3.markdown(_kpi("Files Merged",   stats.get("files", 1),   "amber"),  unsafe_allow_html=True)
    c4.markdown(_kpi("Memory (MB)",    stats["memory_mb"],       "purple"), unsafe_allow_html=True)
    st.caption(f"Files: {', '.join(names)}")


def _preview(df: pd.DataFrame):
    with st.expander("👁️ Preview Data (first 200 rows)", expanded=False):
        st.dataframe(df.head(200), use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — COLUMN MAPPING
# ══════════════════════════════════════════════════════════════════════════════

def page_mapping():
    _require_df()
    df = st.session_state.df

    _section("🔗 Step 2 — Map Dataset Columns to Audit Fields")
    st.info(
        "Map your file's column names to standard audit fields. "
        "Fields already detected are pre-filled. "
        "Unmapped fields are ignored during analysis.",
        icon="ℹ️",
    )

    upload_cols = ["— Not Available —"] + df.columns.tolist()
    col_map     = st.session_state.col_map.copy()

    items = list(REQUIRED_FIELDS.items())
    cols_per_row = 3

    # Group into sections for clarity
    _field_groups = [
        ("Document & GL Fields",
         ["doc_number","doc_date","posting_date","entry_date","doc_type","posting_key",
          "gl_account","gl_desc","amount","dc_indicator"]),
        ("Vendor & Customer Fields",
         ["vendor_code","vendor_name","customer","invoice_ref","purchase_doc","clearing_doc"]),
        ("User & Approval Fields",
         ["user_id","approval_id","assignment","reference"]),
        ("Narrative & Classification Fields",
         ["text","reversal_indicator","cost_center","profit_center","offset_account",
          "wbs","sales_doc","billing_doc","asset","order","wht_amount","collective_inv"]),
        ("Grouping & Analytics Fields",
         ["grouping","sub_grouping"]),
    ]

    for group_label, keys in _field_groups:
        with st.expander(f"**{group_label}**", expanded=True):
            group_items = [(k, REQUIRED_FIELDS[k]) for k in keys if k in REQUIRED_FIELDS]
            for row_start in range(0, len(group_items), cols_per_row):
                row_items = group_items[row_start:row_start + cols_per_row]
                c_cols = st.columns(cols_per_row)
                for j, (key, label) in enumerate(row_items):
                    cur = col_map.get(key)
                    default_idx = upload_cols.index(cur) if cur and cur in upload_cols else 0
                    selected = c_cols[j].selectbox(
                        label, upload_cols, index=default_idx, key=f"map_{key}",
                    )
                    col_map[key] = None if selected == "— Not Available —" else selected

    if st.button("💾 Save Column Mapping", type="primary"):
        st.session_state.col_map = col_map
        st.session_state.run_done= False
        mapped_count = sum(1 for v in col_map.values() if v)
        st.success(f"✅ Column mapping saved — {mapped_count}/{len(REQUIRED_FIELDS)} fields mapped.")

    # Mapping status
    _section("📊 Mapping Status")
    mapped   = {k: v for k, v in col_map.items() if v}
    unmapped = {k: REQUIRED_FIELDS[k] for k, v in col_map.items() if not v}
    c1, c2 = st.columns(2)
    with c1:
        st.caption(f"**✅ Mapped ({len(mapped)})**")
        for k, v in mapped.items():
            st.markdown(f"- `{REQUIRED_FIELDS[k]}` → `{v}`")
    with c2:
        st.caption(f"**⚪ Unmapped ({len(unmapped)}) — will be skipped**")
        for k, v in unmapped.items():
            st.markdown(f"- `{v}`")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — AUDIT PARAMETERS
# ══════════════════════════════════════════════════════════════════════════════

def page_parameters():
    _section("⚙️ Step 3 — Set Audit Parameters")

    p = st.session_state.params or {}

    # ── Amount unit selector (outside form → immediate re-render on change) ──
    st.markdown("##### 💱 Amount Entry Unit")
    _UNITS = {
        "₹ Rupees":                    1,
        "Lakhs  (1 L = ₹ 1,00,000)":  1_00_000,
        "Crores (1 Cr = ₹ 1,00,00,000)": 1_00_00_000,
    }
    _unit_labels = list(_UNITS.keys())
    _saved_unit_idx = p.get("_amt_unit_idx", 1)          # default: Lakhs
    amt_unit = st.radio(
        "Select the unit for all monetary inputs below",
        _unit_labels, index=_saved_unit_idx, horizontal=True,
        key="amt_unit_radio",
    )
    _mult      = _UNITS[amt_unit]
    _unit_idx  = _unit_labels.index(amt_unit)
    _is_rupees = _mult == 1
    _num_fmt   = "%.0f" if _is_rupees else "%.2f"
    _unit_tag  = "₹" if _is_rupees else amt_unit.split()[0]   # "₹", "Lakhs", "Crores"

    def _display(stored_rupees: float) -> float:
        """Convert stored absolute ₹ value to the currently selected unit."""
        return round(stored_rupees / _mult, 4)

    def _step(base_rupees: float) -> float:
        return max(round(base_rupees / _mult, 4), 0.01)

    st.caption(
        f"All amounts below are entered in **{_unit_tag}**. "
        f"They will be stored as absolute ₹ values internally."
    )
    st.divider()

    with st.form("params_form"):
        st.markdown("#### Client Information *(for working paper)*")
        cc1, cc2 = st.columns(2)
        client_name   = cc1.text_input("Client Name",   value=p.get("client_name",   ""))
        fin_year      = cc2.text_input("Financial Year", value=p.get("financial_year", "FY 2025-26"))
        cp1, cp2      = st.columns(2)
        prepared_by   = cp1.text_input("Prepared By",   value=p.get("prepared_by",   ""))
        reviewed_by   = cp2.text_input("Reviewed By",   value=p.get("reviewed_by",   ""))

        st.divider()
        st.markdown(f"#### 💰 Materiality Settings  *(entered in {_unit_tag})*")
        m1, m2, m3 = st.columns(3)
        overall_mat_v = m1.number_input(
            f"Overall Materiality ({_unit_tag})",
            value=_display(float(p.get("overall_materiality", 10_00_000))),
            min_value=0.0, step=_step(1_00_000), format=_num_fmt,
        )
        perf_mat_v = m2.number_input(
            f"Performance Materiality ({_unit_tag})",
            value=_display(float(p.get("performance_materiality", 7_50_000))),
            min_value=0.0, step=_step(50_000), format=_num_fmt,
        )
        trivial_v = m3.number_input(
            f"Trivial / Clearly Trivial ({_unit_tag})",
            value=_display(float(p.get("trivial_threshold", 50_000))),
            min_value=0.0, step=_step(5_000), format=_num_fmt,
        )
        # Live rupee equivalent preview
        st.caption(
            f"Equivalent in ₹ — "
            f"Overall: ₹{overall_mat_v * _mult:,.0f}  |  "
            f"Performance: ₹{perf_mat_v * _mult:,.0f}  |  "
            f"Trivial: ₹{trivial_v * _mult:,.0f}"
        )

        st.divider()
        st.markdown(f"#### 🎛️ Control Limit Settings  *(entered in {_unit_tag})*")
        cl1, cl2, cl3 = st.columns(3)
        approval_lim_v = cl1.number_input(
            f"Approval Limit ({_unit_tag})",
            value=_display(float(p.get("approval_limit", 5_00_000))),
            min_value=0.0, step=_step(50_000), format=_num_fmt,
        )
        round_thresh_v = cl2.number_input(
            f"Round Number Threshold ({_unit_tag})",
            value=_display(float(p.get("round_number_threshold", 10_000))),
            min_value=0.0, step=_step(10_000), format=_num_fmt,
        )
        top_n_users = cl3.number_input(
            "Top N High-Volume Users",
            value=int(p.get("top_n_users", 5)), min_value=1, max_value=20, step=1,
        )
        st.caption(
            f"Equivalent in ₹ — "
            f"Approval Limit: ₹{approval_lim_v * _mult:,.0f}  |  "
            f"Round Threshold: ₹{round_thresh_v * _mult:,.0f}"
        )

        cl4, cl5, cl6 = st.columns(3)
        burst_thresh  = cl4.number_input("Burst Posting Threshold (entries/hr)", value=int(p.get("burst_posting_threshold", 30)), min_value=5, step=5)
        dormancy_days = cl5.number_input("Dormancy Days (User Inactivity)",       value=int(p.get("dormancy_days", 90)),  min_value=10, step=10)
        new_vendor_d  = cl6.number_input("New Vendor Days (Created & Paid)",      value=int(p.get("new_vendor_days", 30)), min_value=1,  step=5)

        st.divider()
        st.markdown("#### 📅 Audit Period & Year-End")
        d1, d2, d3, d4 = st.columns(4)
        _def_start = p.get("start_date") or date(2025, 4, 1)
        _def_end   = p.get("end_date")   or date(2026, 3, 31)
        _def_ye    = p.get("year_end_date") or date(2026, 3, 31)

        start_date    = d1.date_input("Audit Start Date",    value=_def_start)
        end_date      = d2.date_input("Audit End Date",      value=_def_end)
        year_end      = d3.date_input("Financial Year-End",  value=_def_ye)
        spike_days    = d4.number_input("Year-End Spike Window (days)", value=int(p.get("revenue_spike_days", 5)), min_value=1, max_value=30)

        sys_lock_raw  = st.text_input("System Lock Date (YYYY-MM-DD, optional)", value=str(p.get("system_lock_date","")) or "")

        st.divider()
        st.markdown("#### 🤖 Advanced / Optional")
        run_ml = st.checkbox("Enable ML Anomaly Detection (Isolation Forest)", value=p.get("run_ml", False))
        benford_thr = st.slider("Benford Deviation Threshold (%)", 2, 15, int(p.get("benford_deviation_threshold", 5) * 100 if p.get("benford_deviation_threshold", 0.05) < 1 else p.get("benford_deviation_threshold", 5)), 1)

        submitted = st.form_submit_button("💾 Save Parameters", type="primary")

    if submitted:
        st.session_state.params = {
            "client_name":              client_name,
            "financial_year":           fin_year,
            "prepared_by":              prepared_by,
            "reviewed_by":              reviewed_by,
            # Store all monetary values as absolute ₹
            "overall_materiality":      overall_mat_v  * _mult,
            "performance_materiality":  perf_mat_v     * _mult,
            "trivial_threshold":        trivial_v      * _mult,
            "approval_limit":           approval_lim_v * _mult,
            "round_number_threshold":   round_thresh_v * _mult,
            "top_n_users":              int(top_n_users),
            "burst_posting_threshold":  int(burst_thresh),
            "dormancy_days":            int(dormancy_days),
            "new_vendor_days":          int(new_vendor_d),
            "start_date":               start_date,
            "end_date":                 end_date,
            "year_end_date":            year_end,
            "revenue_spike_days":       int(spike_days),
            "system_lock_date":         sys_lock_raw.strip() or None,
            "run_ml":                   run_ml,
            "benford_deviation_threshold": benford_thr / 100,
            "_amt_unit_idx":            _unit_idx,     # remember last used unit
        }
        st.session_state.run_done = False
        st.success(
            f"✅ Parameters saved.  "
            f"Overall Materiality = ₹{overall_mat_v * _mult:,.0f}  |  "
            f"Approval Limit = ₹{approval_lim_v * _mult:,.0f}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — RUN JE TESTS
# ══════════════════════════════════════════════════════════════════════════════

def page_run():
    _require_df()
    _section("🚀 Step 4 — Run Exception Analysis")

    df       = st.session_state.df
    col_map  = st.session_state.col_map or {}
    params   = st.session_state.params  or {}

    if not params:
        st.warning("⚠️ No audit parameters set. Using defaults.")

    # Test selection
    st.markdown("##### Select Tests to Run")
    cc = st.columns(3)
    run_amount  = cc[0].checkbox("Amount-Based Tests",       value=True)
    run_timing  = cc[1].checkbox("Timing-Based Tests",       value=True)
    run_user    = cc[2].checkbox("User & Access Control",    value=True)
    run_vendor  = cc[0].checkbox("Vendor Master Data Tests", value=True)
    run_benford = cc[1].checkbox("Benford's Law Analysis",   value=True)
    run_ml      = cc[2].checkbox("ML Anomaly Detection",     value=params.get("run_ml", False))

    st.divider()
    c1, c2 = st.columns([2, 1])
    go_btn      = c1.button("▶️  Run JE Exception Tests",   type="primary", use_container_width=True)
    refresh_btn = c2.button("🔄 Refresh / Re-Run",         use_container_width=True)

    if go_btn or refresh_btn:
        all_exceptions: list = []

        # Inject master data into params so all test functions can access them
        params["doc_type_map"]       = st.session_state.get("doc_type_map") or build_category_map()
        params["vendor_master_df"]   = st.session_state.get("vendor_master_df")
        params["related_party_list"] = st.session_state.get("related_party_list", [])
        params["custom_holidays"]    = st.session_state.get("custom_holidays", {})

        # ── Pre-compute full-df derived params (used inside row-level chunks) ──
        _amt_col = col_map.get("amount")
        if _amt_col and _amt_col in df.columns:
            _abs_full = pd.to_numeric(df[_amt_col], errors="coerce").abs().fillna(0)
            _med = _abs_full.median()
            _pm  = float(params.get("performance_materiality",
                         float(params.get("overall_materiality", 1_000_000)) * 0.75))
            params["_hv_thresh"] = max(_med * 10, _pm * 0.1) if _med > 0 else _pm * 0.1
        else:
            params["_hv_thresh"] = float(params.get("performance_materiality",
                                    float(params.get("overall_materiality", 1_000_000)) * 0.75)) * 0.1

        _grp_col = col_map.get("grouping")
        params["_grouping_mapped"] = bool(
            _grp_col and _grp_col in df.columns and
            df[_grp_col].fillna("").astype(str).str.strip().str.len().max() > 0
        )

        # ── Exclude GL-excluded rows before running any tests ─────────────────
        _excl_mask = df.get("_gl_exclude", pd.Series(False, index=df.index)).astype(bool)
        _excl_count = _excl_mask.sum()
        df_test = df[~_excl_mask].copy() if _excl_count > 0 else df
        if _excl_count > 0:
            _excl_groups = (
                df[_excl_mask]["_gl_group"].value_counts().to_dict()
                if "_gl_group" in df.columns else {}
            )
            _excl_summary = ", ".join(f"{g} ({n:,})" for g, n in _excl_groups.items()) or f"{_excl_count:,} rows"
            st.info(
                f"**{_excl_count:,} rows excluded from tests** based on GL Account Groups "
                f"(system-generated / non-testable): {_excl_summary}. "
                f"Testing on **{len(df_test):,}** rows.",
                icon="🚫",
            )

        progress = st.progress(0, text="Initialising…")

        def _step(label: str, pct: int, fn, *args):
            progress.progress(pct, text=label)
            try:
                return fn(*args)
            except Exception as exc:
                st.warning(f"⚠️ {label} — {exc}")
                return []

        # ── Phase 1: Row-level tests — streamed in chunks ──────────────────────
        _CHUNK = 500_000
        _n_chunks = math.ceil(len(df_test) / _CHUNK)
        for _i in range(_n_chunks):
            _chunk = df_test.iloc[_i * _CHUNK : (_i + 1) * _CHUNK]
            _pct   = int(5 + (_i / _n_chunks) * 40)   # 5% → 45%
            progress.progress(_pct, text=f"Row-level scan — chunk {_i + 1}/{_n_chunks}…")
            try:
                if run_amount:
                    all_exceptions += run_amount_rowlevel(_chunk, col_map, params)
                if run_timing:
                    all_exceptions += run_timing_rowlevel(_chunk, col_map, params)
                if run_user:
                    all_exceptions += run_user_rowlevel(_chunk, col_map, params)
            except Exception as _exc_e:
                st.warning(f"⚠️ Chunk {_i + 1} row-level error — {_exc_e}")

        # ── Phase 2: Aggregation tests — run on excluded-filtered dataset ────────
        if run_amount:
            all_exceptions += _step("Amount Aggregation…", 50, run_amount_aggregation, df_test, col_map, params)
        if run_timing:
            all_exceptions += _step("Timing Aggregation…", 60, run_timing_aggregation, df_test, col_map, params)
        if run_user:
            all_exceptions += _step("User Aggregation…",   70, run_user_aggregation,   df_test, col_map, params)
        if run_vendor:
            all_exceptions += _step("Vendor Tests…",       78, run_vendor_tests,       df_test, col_map, params)

        # Benford
        b_exc, b_df, chi2_df = [], pd.DataFrame(), pd.DataFrame()
        if run_benford:
            progress.progress(82, text="Benford's Law…")
            try:
                b_exc, b_df, chi2_df = run_benford_test(df_test, col_map, params)
                all_exceptions += b_exc
            except Exception as exc:
                st.warning(f"⚠️ Benford test: {exc}")

        # ML Anomaly Detection
        if run_ml:
            progress.progress(90, text="ML Anomaly Detection…")
            try:
                ml_exc = _run_isolation_forest(df_test, col_map, params)
                all_exceptions += ml_exc
            except ImportError:
                st.info("sklearn not installed — ML detection skipped.")
            except Exception as exc:
                st.warning(f"⚠️ ML test: {exc}")

        progress.progress(95, text="Aggregating results…")

        exc_df = pd.DataFrame(all_exceptions) if all_exceptions else pd.DataFrame(
            columns=["original_index","Exception Type","Exception Category","Risk Level","Risk Score","Detail"]
        )

        tx_risk_df   = calculate_transaction_risk(exc_df)
        user_col     = col_map.get("user_id")
        user_risk_df = rank_users(exc_df, df, user_col) if user_col else pd.DataFrame()

        st.session_state.exceptions_df = exc_df
        st.session_state.benford_df    = b_df
        st.session_state.chi2_df       = chi2_df
        st.session_state.tx_risk_df    = tx_risk_df
        st.session_state.user_risk_df  = user_risk_df
        st.session_state.run_done      = True

        progress.progress(100, text="Done ✅")

        # Quick summary
        st.divider()
        if exc_df.empty:
            st.success("✅ No exceptions detected with current parameters.")
        else:
            _show_kpis(exc_df, df)
            st.info("Navigate to **Exception Results** or **Dashboard** for full details.")


def _run_isolation_forest(df: pd.DataFrame, col_map: dict, params: dict) -> list:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler

    feat_keys = ["amount"]
    feat_cols = [col_map.get(k) for k in feat_keys if col_map.get(k) and col_map[k] in df.columns]
    if not feat_cols:
        return []

    X = df[feat_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
    # Add derived features
    amt = X.iloc[:, 0].abs()
    X = pd.DataFrame({"amt": amt, "log_amt": np.log1p(amt)})

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    clf = IsolationForest(contamination=0.05, random_state=42, n_jobs=-1)
    labels = clf.fit_predict(X_scaled)

    anomaly_idx = df.index[labels == -1].tolist()

    # Filter: only report anomalies above performance materiality — avoids flagging small items
    overall_mat = float(params.get("overall_materiality", 1_000_000))
    perf_mat    = float(params.get("performance_materiality", overall_mat * 0.75))
    amt_col     = col_map.get("amount", "")
    if amt_col and amt_col in df.columns:
        try:
            _amt_abs    = pd.to_numeric(df[amt_col], errors="coerce").abs().fillna(0)
            anomaly_idx = [i for i in anomaly_idx if _amt_abs.at[i] >= perf_mat]
        except Exception:
            pass

    return [
        {
            "original_index":    i,
            "Exception Type":    "ML Anomaly (Isolation Forest)",
            "Exception Category":"System & Entry Nature",
            "Risk Level":        "High",
            "Risk Score":        5,
            "Detail":            f"High-value transaction flagged as statistical anomaly by Isolation Forest (≥ ₹{perf_mat:,.0f} performance materiality floor applied)",
        }
        for i in anomaly_idx
    ]


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 5 — EXCEPTION RESULTS
# ══════════════════════════════════════════════════════════════════════════════

def page_exceptions():
    _require_df()
    _require_run()

    exc_df = st.session_state.exceptions_df
    df     = st.session_state.df

    _section("📋 Step 5 — Exception Results")

    if exc_df is None or exc_df.empty:
        st.success("✅ No exceptions found — clean dataset!")
        return

    _show_kpis(exc_df, df)

    # ── Filters ──────────────────────────────────────────────────────────────
    st.markdown("##### Filter Exceptions")
    f1, f2, f3 = st.columns(3)
    all_cats   = sorted(exc_df["Exception Category"].unique().tolist())
    all_risks  = ["Critical", "High", "Medium", "Low"]
    sel_cats   = f1.multiselect("Category",   all_cats,  default=all_cats)
    sel_risks  = f2.multiselect("Risk Level", all_risks, default=all_risks)
    kw_search  = f3.text_input("🔍 Keyword Search (narration / type / detail)", "")

    filtered = exc_df[
        exc_df["Exception Category"].isin(sel_cats) &
        exc_df["Risk Level"].isin(sel_risks)
    ]
    if kw_search.strip():
        kw = kw_search.lower()
        mask = (
            filtered["Exception Type"].str.lower().str.contains(kw, na=False) |
            filtered["Detail"].str.lower().str.contains(kw, na=False)
        )
        filtered = filtered[mask]

    st.caption(f"Showing **{len(filtered):,}** exceptions · {filtered['original_index'].nunique():,} unique transactions")

    # ── Summary table ─────────────────────────────────────────────────────────
    _section("📊 Summary by Category & Type")
    summary = (
        filtered
        .groupby(["Exception Category", "Exception Type", "Risk Level"])
        .agg(Count=("original_index","count"), Unique_Txns=("original_index","nunique"))
        .reset_index()
        .sort_values("Count", ascending=False)
    )
    st.dataframe(
        summary.style.map(_risk_style, subset=["Risk Level"]),
        use_container_width=True, height=280,
    )

    # ── Detail by category ────────────────────────────────────────────────────
    _section("🔎 Exception Detail by Category")
    for cat in filtered["Exception Category"].unique():
        cat_df = filtered[filtered["Exception Category"] == cat]
        merged = cat_df.merge(
            df.reset_index().rename(columns={"index":"original_index"}),
            on="original_index", how="left",
        )
        with st.expander(f"**{cat}** — {len(cat_df):,} exceptions | {cat_df['original_index'].nunique():,} transactions"):
            st.dataframe(merged, use_container_width=True, height=280)

    # ── Transaction Risk Ranking ──────────────────────────────────────────────
    _section("🏆 Top Risky Transactions")
    tx_risk = st.session_state.tx_risk_df
    if tx_risk is not None and not tx_risk.empty:
        top_tx = tx_risk.head(50).merge(
            df.reset_index().rename(columns={"index":"original_index"}),
            on="original_index", how="left",
        )
        st.dataframe(top_tx, use_container_width=True, height=300)

    # ── User Risk Ranking ─────────────────────────────────────────────────────
    _section("👤 User Risk Ranking")
    user_risk = st.session_state.user_risk_df
    if user_risk is not None and not user_risk.empty:
        st.dataframe(
            user_risk.style.map(_risk_style, subset=["Risk_Band"]),
            use_container_width=True, height=280,
        )

    # ── Group by Document Number ──────────────────────────────────────────────
    _section("📂 Exceptions Grouped by Document Number")
    col_map = st.session_state.col_map or {}
    doc_col = col_map.get("doc_number")
    if doc_col and doc_col in df.columns:
        _doc_merged = filtered.merge(
            df[[doc_col]].reset_index().rename(columns={"index": "original_index"}),
            on="original_index", how="left",
        )
        _doc_grouped = (
            _doc_merged.groupby(doc_col)
            .agg(
                Exception_Count=("Exception Type", "count"),
                Unique_Exception_Types=("Exception Type", lambda x: ", ".join(sorted(x.unique()))),
                Risk_Levels=("Risk Level", lambda x: ", ".join(sorted(x.unique()))),
                Highest_Risk=("Risk Level", lambda x: (
                    "Critical" if "Critical" in x.values else
                    "High"     if "High"     in x.values else
                    "Medium"   if "Medium"   in x.values else "Low"
                )),
            )
            .reset_index()
            .sort_values("Exception_Count", ascending=False)
        )
        st.caption(f"{len(_doc_grouped):,} unique documents flagged across {len(filtered):,} exceptions")
        st.dataframe(
            _doc_grouped.style.map(_risk_style, subset=["Highest_Risk"]),
            use_container_width=True, height=340,
        )
        # Download
        _ts = datetime.now().strftime("%Y%m%d_%H%M")
        _client = (st.session_state.get("params", {}).get("client_name") or "Client").replace(" ", "_")
        _doc_csv = _df_to_csv_bytes(_doc_grouped)
        st.download_button(
            label="📥 Download Doc-Number Summary (CSV)",
            data=_doc_csv,
            file_name=f"JE_DocGrouped_{_client}_{_ts}.csv",
            mime="text/csv",
            use_container_width=False,
        )
    else:
        st.info("Document number column not mapped — go to Column Mapping to set it.", icon="ℹ️")

    # ── Sampling Tool ─────────────────────────────────────────────────────────
    _section("🎯 Sampling Tool")
    s1, s2, s3 = st.columns(3)
    sample_method = s1.selectbox("Sample Method", ["Top Risky (by Score)", "Random Sample", "Stratified by Risk"])
    sample_size   = s2.number_input("Sample Size", min_value=5, max_value=min(500, len(filtered)), value=min(50, len(filtered)), step=5)
    if s3.button("Generate Sample"):
        _generate_sample(filtered, df, sample_method, int(sample_size))


def _generate_sample(filtered, df, method, n):
    tx_risk = st.session_state.tx_risk_df
    if method == "Top Risky (by Score)" and tx_risk is not None and not tx_risk.empty:
        top_idx   = tx_risk.head(n)["original_index"].tolist()
        sample_df = df.loc[df.index.isin(top_idx)].head(n)
    elif method == "Stratified by Risk":
        frames = []
        for rl in ["Critical","High","Medium","Low"]:
            sub = filtered[filtered["Risk Level"] == rl]
            k   = max(1, int(n * {"Critical":0.4,"High":0.3,"Medium":0.2,"Low":0.1}[rl]))
            frames.append(sub.sample(min(k, len(sub)), random_state=42) if len(sub) > 0 else sub)
        sample_df = pd.concat(frames).head(n).merge(
            df.reset_index().rename(columns={"index":"original_index"}),
            on="original_index", how="left",
        )
    else:
        sample_df = filtered.sample(min(n, len(filtered)), random_state=42).merge(
            df.reset_index().rename(columns={"index":"original_index"}),
            on="original_index", how="left",
        )
    st.markdown(f"**Sample ({len(sample_df)} items)**")
    st.dataframe(sample_df, use_container_width=True, height=300)


def _risk_style(val):
    colors = {"Critical":"#FF3B3B","High":"#FF8C00","Medium":"#F5A623","Low":"#4CAF50"}
    c = colors.get(str(val), "")
    return f"background-color:{c};color:white;font-weight:bold" if c else ""


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 6 — DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

def page_dashboard():
    _require_df()
    _require_run()

    exc_df  = st.session_state.exceptions_df
    df      = st.session_state.df
    col_map = st.session_state.col_map or {}

    _section("📊 Step 6 — Analytics Dashboard")

    if exc_df is None or exc_df.empty:
        st.success("✅ No exceptions — nothing to visualise.")
        return

    _show_kpis(exc_df, df)

    # ── Row 1: Category bar + Risk pie ───────────────────────────────────────
    col1, col2 = st.columns([3, 2])
    with col1:
        cat_s = (exc_df.groupby("Exception Category")
                 .agg(Count=("original_index","count")).reset_index()
                 .sort_values("Count", ascending=True))
        fig = px.bar(
            cat_s, x="Count", y="Exception Category", orientation="h",
            title="Exceptions by Category",
            color="Count", color_continuous_scale="Blues",
            template="plotly_white",
        )
        fig.update_layout(margin=dict(l=0,r=0,t=40,b=0), height=340, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        rl_s = exc_df["Risk Level"].value_counts().reset_index()
        rl_s.columns = ["Risk Level","Count"]
        color_map = {"Critical":"#FF3B3B","High":"#FF8C00","Medium":"#F5A623","Low":"#4CAF50"}
        fig2 = px.pie(
            rl_s, names="Risk Level", values="Count",
            title="Risk Level Distribution",
            color="Risk Level", color_discrete_map=color_map,
            hole=0.4,
        )
        fig2.update_layout(margin=dict(l=0,r=0,t=40,b=0), height=340)
        st.plotly_chart(fig2, use_container_width=True)

    # ── Row 2: User exceptions bar + Exception type bar ───────────────────────
    user_col = col_map.get("user_id")
    col3, col4 = st.columns(2)
    with col3:
        if user_col and user_col in df.columns:
            user_risk = st.session_state.user_risk_df
            if user_risk is not None and not user_risk.empty:
                top_u = user_risk.head(15)
                fig3 = px.bar(
                    top_u, x=user_col, y="Total_Exceptions",
                    title="Top Users by Exception Count",
                    color="Total_Risk_Score", color_continuous_scale="Reds",
                    template="plotly_white",
                )
                fig3.update_layout(margin=dict(l=0,r=0,t=40,b=0), height=320)
                st.plotly_chart(fig3, use_container_width=True)

    with col4:
        exc_type_s = (exc_df.groupby("Exception Type")
                      .size().reset_index(name="Count")
                      .sort_values("Count", ascending=False).head(15))
        fig4 = px.bar(
            exc_type_s, x="Count", y="Exception Type", orientation="h",
            title="Top Exception Types",
            color="Count", color_continuous_scale="Oranges",
            template="plotly_white",
        )
        fig4.update_layout(margin=dict(l=0,r=0,t=40,b=0), height=320, showlegend=False)
        st.plotly_chart(fig4, use_container_width=True)

    # ── Posting Heatmap (day of week × hour) ─────────────────────────────────
    _section("📅 Daily Posting Activity Heatmap")
    pd_col = col_map.get("posting_date")
    if pd_col and pd_col in df.columns:
        dates = pd.to_datetime(df[pd_col], errors="coerce").dropna()
        if len(dates) > 10:
            heat = (
                pd.DataFrame({"day": dates.dt.day_name(), "hour": dates.dt.hour})
                .groupby(["day","hour"]).size().reset_index(name="count")
            )
            day_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            heat["day"] = pd.Categorical(heat["day"], categories=day_order, ordered=True)
            heat = heat.sort_values("day")
            pivot = heat.pivot(index="day", columns="hour", values="count").fillna(0)
            fig5  = px.imshow(
                pivot, title="Posting Heatmap (Day × Hour)",
                color_continuous_scale="YlOrRd",
                labels=dict(x="Hour of Day", y="Day of Week", color="Entries"),
                aspect="auto",
            )
            fig5.update_layout(margin=dict(l=0,r=0,t=40,b=10), height=320)
            st.plotly_chart(fig5, use_container_width=True)

    # ── Exception Heatmap (User × Category) ──────────────────────────────────
    _section("🔥 Exception Heatmap — User × Category")
    if user_col and user_col in df.columns and not exc_df.empty:
        user_risk = st.session_state.user_risk_df
        if user_risk is not None and not user_risk.empty:
            exc_with_user = exc_df.merge(
                df[[user_col]].reset_index().rename(columns={"index":"original_index"}),
                on="original_index", how="left",
            )
            top_users = exc_with_user[user_col].value_counts().head(15).index.tolist()
            sub = exc_with_user[exc_with_user[user_col].isin(top_users)]
            pivot_u = (sub.groupby([user_col,"Exception Category"])
                       .size().reset_index(name="count")
                       .pivot(index=user_col, columns="Exception Category", values="count")
                       .fillna(0))
            fig6 = px.imshow(
                pivot_u, title="User vs Exception Category Heatmap",
                color_continuous_scale="Reds", aspect="auto",
            )
            fig6.update_layout(margin=dict(l=0,r=0,t=40,b=0), height=400)
            st.plotly_chart(fig6, use_container_width=True)

    # ── Benford Distribution ──────────────────────────────────────────────────
    _section("📐 Benford's Law Distribution")
    bdf = st.session_state.benford_df
    if bdf is not None and not bdf.empty:
        fig7 = go.Figure()
        fig7.add_trace(go.Bar(
            x=bdf["First Digit"], y=bdf["Observed %"],
            name="Observed", marker_color="#2d6a9f",
        ))
        fig7.add_trace(go.Scatter(
            x=bdf["First Digit"], y=bdf["Expected % (Benford)"],
            name="Benford Expected", mode="lines+markers",
            line=dict(color="#e74c3c", width=2, dash="dash"),
        ))
        fig7.update_layout(
            title="Observed vs Expected First-Digit Distribution (Benford's Law)",
            xaxis_title="First Digit", yaxis_title="Percentage (%)",
            template="plotly_white", height=360,
            margin=dict(l=0,r=0,t=40,b=0),
        )
        st.plotly_chart(fig7, use_container_width=True)

        c_df = st.session_state.chi2_df
        if c_df is not None and not c_df.empty:
            with st.expander("Chi-Square Test Details"):
                st.dataframe(c_df, use_container_width=True)

    else:
        st.info("Benford analysis not run or insufficient data.")

    # ── Monthly Trend ─────────────────────────────────────────────────────────
    _section("📈 Monthly Exception Trend")
    if pd_col and pd_col in df.columns and not exc_df.empty:
        exc_with_date = exc_df.merge(
            df[[pd_col]].reset_index().rename(columns={"index":"original_index"}),
            on="original_index", how="left",
        )
        exc_with_date["month"] = pd.to_datetime(exc_with_date[pd_col], errors="coerce").dt.to_period("M").astype(str)
        monthly = exc_with_date.groupby(["month","Risk Level"]).size().reset_index(name="count")
        fig8 = px.bar(
            monthly, x="month", y="count", color="Risk Level",
            title="Exceptions per Month by Risk Level",
            color_discrete_map={"Critical":"#FF3B3B","High":"#FF8C00","Medium":"#F5A623","Low":"#4CAF50"},
            template="plotly_white",
        )
        fig8.update_layout(margin=dict(l=0,r=0,t=40,b=0), height=320)
        st.plotly_chart(fig8, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 7 — EXPORT RESULTS
# ══════════════════════════════════════════════════════════════════════════════

def page_export():
    _require_df()
    _require_run()

    exc_df  = st.session_state.exceptions_df
    df      = st.session_state.df
    col_map = st.session_state.col_map or {}
    params  = st.session_state.params  or {}

    _section("⬇️ Step 7 — Export Audit Evidence")

    st.markdown("""
    The Excel export contains the following worksheets:
    | Sheet | Contents |
    |---|---|
    | **Exception Transactions** | All flagged transactions with risk level and detail |
    | **Summary by Risk** | Grouped summary by category, type, and risk |
    | **Dashboard Data** | Aggregated data for charts and analytics |
    | **Benford Results** | Digit distribution statistics and chi-square test |
    | **Audit Working Paper** | JE Testing summary, exception narrative, and audit conclusion |
    """)

    c1, c2 = st.columns(2)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    client = (params.get("client_name") or "Client").replace(" ", "_")

    with c1:
        if st.button("🔨 Generate Full Audit Report", type="primary", use_container_width=True):
            with st.spinner("Building Excel report…"):
                try:
                    report_bytes = to_excel_report(
                        df_orig      = df,
                        exceptions_df= exc_df if exc_df is not None else pd.DataFrame(),
                        benford_df   = st.session_state.benford_df,
                        chi2_df      = st.session_state.chi2_df,
                        audit_params = params,
                        col_map      = col_map,
                    )
                    st.download_button(
                        label    = "📥 Download Full Report (Excel)",
                        data     = report_bytes,
                        file_name= f"JE_Audit_Report_{client}_{ts}.xlsx",
                        mime     = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
                    st.success("✅ Report ready — click Download above.")
                except Exception as exc:
                    st.error(f"Export failed: {exc}")

    with c2:
        # Quick CSV export of exceptions
        if exc_df is not None and not exc_df.empty:
            merged_export = exc_df.merge(
                df.reset_index().rename(columns={"index":"original_index"}),
                on="original_index", how="left",
            )
            csv_bytes = _df_to_csv_bytes(merged_export)
            st.download_button(
                label    = "📊 Download Exceptions as CSV",
                data     = csv_bytes,
                file_name= f"JE_Exceptions_{client}_{ts}.csv",
                mime     = "text/csv",
                use_container_width=True,
            )

    # Audit Working Paper text preview
    _section("📄 Audit Working Paper Preview")
    if exc_df is not None:
        total_exc   = len(exc_df)
        unique_txns = exc_df["original_index"].nunique()
        exc_rate    = round(unique_txns / max(len(df),1) * 100, 1)
        high_ct     = len(exc_df[exc_df["Risk Level"].isin(["High","Critical"])])

        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown("**Engagement Details**")
            st.markdown(f"- Client: `{params.get('client_name','—')}`")
            st.markdown(f"- Period: `{params.get('start_date','—')}` to `{params.get('end_date','—')}`")
            st.markdown(f"- Overall Materiality: `₹{params.get('overall_materiality',0):,.0f}`")
            st.markdown(f"- Prepared by: `{params.get('prepared_by','—')}`")
        with col_r:
            st.markdown("**JE Testing Summary**")
            st.markdown(f"- Total JE Lines: `{len(df):,}`")
            st.markdown(f"- Total Exceptions: `{total_exc:,}`")
            st.markdown(f"- Unique Transactions Flagged: `{unique_txns:,}` ({exc_rate}%)")
            st.markdown(f"- High / Critical Exceptions: `{high_ct:,}`")

        st.markdown("**Audit Conclusion**")
        if high_ct > 50 or exc_rate > 10:
            st.error("**SIGNIFICANT RISK** — Extended procedures recommended. Multiple high/critical exceptions identified.")
        elif high_ct > 10 or exc_rate > 3:
            st.warning("**MODERATE RISK** — Follow-up required on flagged items. Obtain management explanations.")
        else:
            st.success("**LOW RISK** — Exception rate within acceptable range. Standard follow-up procedures apply.")


# ══════════════════════════════════════════════════════════════════════════════
# SHARED KPI PANEL
# ══════════════════════════════════════════════════════════════════════════════

def _show_kpis(exc_df: pd.DataFrame, df: pd.DataFrame):
    total_exc   = len(exc_df)
    unique_txns = exc_df["original_index"].nunique()
    cats_hit    = exc_df["Exception Category"].nunique()
    exc_rate    = round(unique_txns / max(len(df), 1) * 100, 1)
    high_ct     = len(exc_df[exc_df["Risk Level"].isin(["High","Critical"])])

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.markdown(_kpi("Total Exceptions",   f"{total_exc:,}",  "red"),    unsafe_allow_html=True)
    k2.markdown(_kpi("Unique Txns Flagged",f"{unique_txns:,}", "amber"),  unsafe_allow_html=True)
    k3.markdown(_kpi("High/Critical",      f"{high_ct:,}",    "red"),    unsafe_allow_html=True)
    k4.markdown(_kpi("Categories Triggered",cats_hit,          "blue"),   unsafe_allow_html=True)
    k5.markdown(_kpi("Exception Rate",     f"{exc_rate}%",    "green"),  unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 8 — GL ACCOUNT GROUPS
# ══════════════════════════════════════════════════════════════════════════════

def page_gl_groups():
    _require_df()

    df      = st.session_state.df
    col_map = st.session_state.col_map or {}
    gl_col  = col_map.get("gl_account")

    _section("🗂️ GL Account Groups & Subgroups")

    st.markdown(
        "Define how GL accounts should be grouped (e.g. *Revenue*, *Expenses*) and subgrouped "
        "(e.g. *Sales Revenue*, *Admin Expenses*). These labels are added as new columns to the "
        "dataset and become available in the **Data Explorer** pivot table."
    )

    if not gl_col or gl_col not in df.columns:
        st.warning("⚠️ GL Account column is not mapped. Go to **Column Mapping** to set it first.")
        return

    # ── Unique GL accounts for reference ─────────────────────────────────────
    unique_gls = sorted(df[gl_col].dropna().astype(str).unique().tolist())
    with st.expander(f"📋 View all GL accounts in dataset ({len(unique_gls):,})"):
        st.dataframe(pd.DataFrame({"GL Account": unique_gls}), use_container_width=True, height=220)

    # ── Current rule table ────────────────────────────────────────────────────
    rules: list = st.session_state.gl_groups

    _section("📐 Current Grouping Rules")
    if rules:
        rules_df = pd.DataFrame(rules)
        st.dataframe(rules_df, use_container_width=True, height=min(40 + 35 * len(rules), 320))
        if st.button("🗑️ Clear All Rules", type="secondary"):
            st.session_state.gl_groups = []
            # Remove applied columns if present
            for _c in ["_gl_group", "_gl_subgroup"]:
                if _c in st.session_state.df.columns:
                    st.session_state.df.drop(columns=[_c], inplace=True)
            st.rerun()
    else:
        st.info("No rules defined yet. Add rules below.", icon="ℹ️")

    # ── Import rules from CSV ─────────────────────────────────────────────────
    with st.expander("📤 Import / Export Rules (CSV)"):
        _tmpl_cols = ["group", "subgroup", "match_type", "value", "value_end"]
        _tmpl = pd.DataFrame(columns=_tmpl_cols)
        _tmpl_csv = _tmpl.to_csv(index=False).encode("utf-8-sig")
        _ie1, _ie2 = st.columns(2)
        _ie1.download_button(
            "⬇️ Download Template CSV", data=_tmpl_csv,
            file_name="gl_group_template.csv", mime="text/csv",
            use_container_width=True,
        )
        if rules:
            _exp_csv = pd.DataFrame(rules).to_csv(index=False).encode("utf-8-sig")
            _ie2.download_button(
                "⬇️ Export Current Rules", data=_exp_csv,
                file_name="gl_group_rules.csv", mime="text/csv",
                use_container_width=True,
            )
        _up_rules = st.file_uploader(
            "Upload rules CSV (columns: group, subgroup, match_type, value, value_end)",
            type=["csv"], key="gl_rules_upload",
        )
        if _up_rules:
            try:
                _imported = pd.read_csv(_up_rules, dtype=str).fillna("")
                _imported = _imported[[c for c in _tmpl_cols if c in _imported.columns]]
                st.session_state.gl_groups = _imported.to_dict("records")
                st.success(f"✅ Imported {len(_imported)} rules.")
                st.rerun()
            except Exception as _ex:
                st.error(f"Import failed: {_ex}")

    # ── Add rule form ─────────────────────────────────────────────────────────
    _section("➕ Add / Edit Rule")
    st.caption(
        "**match_type options:** `exact` — full account code match · "
        "`prefix` — account starts with value · "
        "`contains` — value appears anywhere · "
        "`range` — numeric range [value … value_end]"
    )
    with st.form("add_gl_rule", clear_on_submit=True):
        _a1, _a2 = st.columns(2)
        _grp    = _a1.text_input("Group",    placeholder="e.g. Revenue")
        _sub    = _a2.text_input("Subgroup", placeholder="e.g. Sales Revenue")
        _b1, _b2, _b3, _b4 = st.columns([2, 2, 2, 2])
        _mtype  = _b1.selectbox("Match Type", ["prefix", "exact", "contains", "range"])
        _val    = _b2.text_input("Value (start)", placeholder="e.g. 4")
        _val2   = _b3.text_input("Value End (range only)", placeholder="e.g. 49999")
        _priority = _b4.number_input("Priority (lower = checked first)", min_value=1, value=len(rules) + 1, step=1)
        _exclude = st.checkbox(
            "🚫 Exclude from JE Exception Tests (system-generated / non-testable accounts, e.g. Depreciation, COGS)",
            value=False,
            help="Rows matching this rule will be removed from the dataset before running tests. "
                 "They remain in the raw data and Data Explorer but are never flagged as exceptions.",
        )
        if st.form_submit_button("Add Rule", type="primary"):
            if not _grp.strip() or not _val.strip():
                st.error("Group and Value are required.")
            else:
                rules.append({
                    "group":      _grp.strip(),
                    "subgroup":   _sub.strip(),
                    "match_type": _mtype,
                    "value":      _val.strip(),
                    "value_end":  _val2.strip(),
                    "priority":   int(_priority),
                    "exclude":    bool(_exclude),
                })
                rules.sort(key=lambda r: r.get("priority", 99))
                st.session_state.gl_groups = rules
                st.success(f"Rule added: [{_grp}] › [{_sub}]" + (" — marked as EXCLUDED from tests" if _exclude else ""))
                st.rerun()

    # ── Apply rules to dataset ────────────────────────────────────────────────
    st.divider()
    _ap1, _ap2 = st.columns([2, 1])
    with _ap1:
        unmatched_label = st.text_input(
            "Label for unmatched GL accounts", value="(Unclassified)",
            help="Rows whose GL account doesn't match any rule get this label."
        )
    if _ap2.button("✅ Apply Groups to Dataset", type="primary", use_container_width=True):
        if not rules:
            st.error("No rules defined. Add at least one rule first.")
        else:
            _gl_series = df[gl_col].astype(str).str.strip()
            _groups    = pd.Series(unmatched_label, index=df.index)
            _subgroups = pd.Series(unmatched_label, index=df.index)
            _excludes  = pd.Series(False,           index=df.index)
            # Apply in reverse-priority order so higher-priority rules win (overwrite lower)
            for _rule in reversed(rules):
                _mt  = _rule.get("match_type", "prefix")
                _v   = str(_rule.get("value", "")).strip()
                _v2  = str(_rule.get("value_end", "")).strip()
                if _mt == "exact":
                    _mask = _gl_series == _v
                elif _mt == "prefix":
                    _mask = _gl_series.str.startswith(_v, na=False)
                elif _mt == "contains":
                    _mask = _gl_series.str.contains(_v, na=False, regex=False)
                elif _mt == "range" and _v and _v2:
                    try:
                        _gl_num = pd.to_numeric(_gl_series, errors="coerce")
                        _mask   = (_gl_num >= float(_v)) & (_gl_num <= float(_v2))
                    except Exception:
                        _mask = pd.Series(False, index=df.index)
                else:
                    _mask = pd.Series(False, index=df.index)
                _groups[_mask]    = _rule.get("group", unmatched_label)
                _subgroups[_mask] = _rule.get("subgroup", unmatched_label) or _rule.get("group", unmatched_label)
                if _rule.get("exclude", False):
                    _excludes[_mask] = True

            st.session_state.df["_gl_group"]    = _groups.values
            st.session_state.df["_gl_subgroup"] = _subgroups.values
            st.session_state.df["_gl_exclude"]  = _excludes.values
            _save_session_cache(st.session_state.df, st.session_state.col_map)

            _matched  = (_groups != unmatched_label).sum()
            _excl_ct  = _excludes.sum()
            st.success(
                f"✅ Groups applied — {_matched:,} / {len(df):,} rows matched "
                f"({round(_matched/max(len(df),1)*100,1)}%). "
                f"**{_excl_ct:,} rows marked as excluded from tests.**"
            )
            # Coverage breakdown with exclude flag
            _cov = (
                st.session_state.df
                .groupby(["_gl_group", "_gl_exclude"])
                .size()
                .reset_index(name="Row Count")
                .rename(columns={"_gl_exclude": "Excluded from Tests"})
                .sort_values("Row Count", ascending=False)
            )
            st.dataframe(_cov, use_container_width=True, height=240)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 9 — DATA EXPLORER (PIVOT TABLE)
# ══════════════════════════════════════════════════════════════════════════════

def page_data_explorer():
    _require_df()

    _section("🔄 Data Explorer — Pivot Table")

    df      = st.session_state.df
    col_map = st.session_state.col_map or {}

    # ── Source selector ───────────────────────────────────────────────────────
    _src_opts = ["Full Dataset"]
    if st.session_state.run_done and st.session_state.exceptions_df is not None and not st.session_state.exceptions_df.empty:
        _src_opts.append("Exceptions Only (merged with data)")
    source = st.radio("Data Source", _src_opts, horizontal=True)

    if source == "Exceptions Only (merged with data)":
        exc_df = st.session_state.exceptions_df
        work_df = exc_df.merge(
            df.reset_index().rename(columns={"index": "original_index"}),
            on="original_index", how="left",
        )
    else:
        work_df = df.copy()

    all_cols = work_df.columns.tolist()

    # ── Optional column-level filter ──────────────────────────────────────────
    with st.expander("🔍 Pre-filter Data (optional)"):
        _fc1, _fc2 = st.columns(2)
        _filter_col = _fc1.selectbox("Filter by column", ["(none)"] + all_cols, key="pe_filter_col")
        if _filter_col != "(none)":
            _unique_vals = sorted(work_df[_filter_col].dropna().astype(str).unique().tolist())
            _sel_vals = _fc2.multiselect(f"Keep values", _unique_vals, default=_unique_vals, key="pe_filter_vals")
            if _sel_vals:
                work_df = work_df[work_df[_filter_col].astype(str).isin(_sel_vals)]
        st.caption(f"Rows after filter: **{len(work_df):,}**")

    st.divider()
    _section("⚙️ Configure Pivot")

    # ── Pivot configuration ───────────────────────────────────────────────────
    _p1, _p2, _p3, _p4 = st.columns(4)

    _num_cols  = [c for c in all_cols if pd.api.types.is_numeric_dtype(work_df[c])]
    _cat_cols  = all_cols  # any column can be a row/column grouper

    row_fields = _p1.multiselect(
        "Rows (group by)", _cat_cols,
        default=[c for c in [col_map.get("gl_account"), "_gl_group", "_gl_subgroup"] if c and c in _cat_cols][:2],
        key="pe_rows",
    )
    col_field = _p2.selectbox(
        "Columns (optional)", ["(none)"] + _cat_cols,
        index=0, key="pe_col",
    )
    value_field = _p3.selectbox(
        "Values", _num_cols if _num_cols else all_cols,
        index=next((i for i, c in enumerate(_num_cols) if c == col_map.get("amount")), 0),
        key="pe_val",
    )
    agg_func = _p4.selectbox(
        "Aggregation", ["sum", "count", "mean", "median", "min", "max", "nunique"],
        key="pe_agg",
    )

    if not row_fields:
        st.info("Select at least one **Row** field to build the pivot.", icon="ℹ️")
        return

    if st.button("▶️ Build Pivot Table", type="primary"):
        try:
            _col_arg = None if col_field == "(none)" else col_field
            pivot = pd.pivot_table(
                work_df,
                index=row_fields,
                columns=_col_arg,
                values=value_field,
                aggfunc=agg_func,
                fill_value=0,
                margins=True,
                margins_name="Grand Total",
            )
            # Flatten multi-level column headers if present
            if isinstance(pivot.columns, pd.MultiIndex):
                pivot.columns = [" | ".join(str(c) for c in col).strip(" | ") for col in pivot.columns]
            pivot = pivot.reset_index()

            st.session_state["_pivot_result"] = pivot
        except Exception as _ex:
            st.error(f"Pivot failed: {_ex}")

    # ── Show result ───────────────────────────────────────────────────────────
    pivot = st.session_state.get("_pivot_result")
    if pivot is not None and not pivot.empty:
        st.caption(f"Pivot: **{len(pivot):,}** rows × **{len(pivot.columns):,}** columns")
        st.dataframe(pivot, use_container_width=True, height=420)

        _ts = datetime.now().strftime("%Y%m%d_%H%M")
        _client = (st.session_state.get("params", {}).get("client_name") or "Client").replace(" ", "_")
        _dl1, _dl2 = st.columns(2)

        # CSV download
        _csv_bytes = _df_to_csv_bytes(pivot)
        _dl1.download_button(
            "📥 Download as CSV",
            data=_csv_bytes,
            file_name=f"JE_Pivot_{_client}_{_ts}.csv",
            mime="text/csv",
            use_container_width=True,
        )

        # Excel download
        _xl_buf = io.BytesIO()
        with pd.ExcelWriter(_xl_buf, engine="xlsxwriter") as _wr:
            pivot.to_excel(_wr, index=False, sheet_name="Pivot")
            _wb  = _wr.book
            _ws  = _wr.sheets["Pivot"]
            _hdr = _wb.add_format({"bold": True, "bg_color": "#1F3864", "font_color": "white", "border": 1})
            for _ci, _cn in enumerate(pivot.columns):
                _ws.write(0, _ci, str(_cn), _hdr)
                _ws.set_column(_ci, _ci, max(14, len(str(_cn)) + 2))
        _xl_buf.seek(0)
        _dl2.download_button(
            "📥 Download as Excel",
            data=_xl_buf,
            file_name=f"JE_Pivot_{_client}_{_ts}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER
# ══════════════════════════════════════════════════════════════════════════════

_route = {
    _PAGES[0]: page_upload,
    _PAGES[1]: page_mapping,
    _PAGES[2]: page_gl_groups,
    _PAGES[3]: page_parameters,
    _PAGES[4]: page_run,
    _PAGES[5]: page_exceptions,
    _PAGES[6]: page_data_explorer,
    _PAGES[7]: page_dashboard,
    _PAGES[8]: page_export,
}

_route[page]()
