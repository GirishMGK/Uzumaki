"""
Uzumaki Parquet Tool  —  Python / Streamlit port of the Sangir Analytics
WPF desktop application (DishaParquetTool).

Rebuilds the desktop app's modules on the pandas / pyarrow / duckdb stack:

  • Convert     — CSV / Excel  ->  Parquet (compression, type inference, batch)
  • Viewer      — inspect a .parquet (schema, row-groups, stats, filter, sort)
  • CSV Tools   — delimiter, encoding, merge, split-rows, schema-compare
  • Analytics   — DuckDB SQL over uploaded parquet / csv, with query history

Exposes `render()` so the unified hub can mount it as a page, and also runs
stand-alone via `streamlit run parquet_tool.py`.
"""

from __future__ import annotations

import io
import os
import sys
import zipfile
from datetime import datetime

import pandas as pd
import streamlit as st

# ── optional engines (import lazily / degrade gracefully) ──────────────────────
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    _HAS_ARROW = True
except Exception:  # pragma: no cover
    _HAS_ARROW = False

try:
    import duckdb
    _HAS_DUCK = True
except Exception:  # pragma: no cover
    _HAS_DUCK = False


COMPRESSIONS = ["snappy", "gzip", "brotli", "zstd", "lz4", "none"]


# ══════════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════════
def _read_tabular(uploaded, *, sep=None, encoding="utf-8", sheet=0) -> pd.DataFrame:
    """Read an uploaded CSV / Excel file into a DataFrame."""
    name = uploaded.name.lower()
    data = uploaded.getvalue()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(data), sheet_name=sheet)
    # CSV / delimited
    kwargs = {"encoding": encoding}
    if sep in (None, "", "auto"):
        kwargs["sep"] = None          # let the python engine sniff
        kwargs["engine"] = "python"
    else:
        kwargs["sep"] = {"\\t": "\t", "tab": "\t"}.get(sep, sep)
    return pd.read_csv(io.BytesIO(data), **kwargs)


def _infer_types(df: pd.DataFrame) -> pd.DataFrame:
    """Best-effort down-cast of object columns to numeric / datetime."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype != object:
            continue
        num = pd.to_numeric(out[col], errors="coerce")
        if num.notna().mean() >= 0.95:
            out[col] = num
            continue
        dt = pd.to_datetime(out[col], errors="coerce", dayfirst=True)
        if dt.notna().mean() >= 0.95:
            out[col] = dt
    return out


def _to_parquet_bytes(df: pd.DataFrame, compression: str) -> bytes:
    comp = None if compression == "none" else compression
    buf = io.BytesIO()
    if _HAS_ARROW:
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, buf, compression=comp or "none")
    else:
        df.to_parquet(buf, compression=comp, index=False)
    return buf.getvalue()


def _parquet_meta(data: bytes) -> dict:
    """Schema / row-group metadata for a parquet byte blob."""
    info = {"columns": [], "num_rows": None, "num_row_groups": None,
            "created_by": None, "size_bytes": len(data)}
    if _HAS_ARROW:
        pf = pq.ParquetFile(io.BytesIO(data))
        md = pf.metadata
        info["num_rows"] = md.num_rows
        info["num_row_groups"] = md.num_row_groups
        info["created_by"] = md.created_by
        for f in pf.schema_arrow:
            info["columns"].append((f.name, str(f.type)))
    return info


# ══════════════════════════════════════════════════════════════════════════════
# module: Convert
# ══════════════════════════════════════════════════════════════════════════════
def _page_convert():
    st.subheader("Convert  ·  CSV / Excel → Parquet")
    files = st.file_uploader(
        "Upload one or more CSV / Excel files",
        type=["csv", "txt", "tsv", "xlsx", "xls"],
        accept_multiple_files=True,
        key="conv_files",
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        compression = st.selectbox("Compression", COMPRESSIONS, index=0, key="conv_comp")
    with c2:
        sep = st.selectbox("CSV delimiter", ["auto", ",", ";", "|", "\\t"], key="conv_sep")
    with c3:
        infer = st.checkbox("Infer column types", value=True, key="conv_infer")

    if not files:
        st.info("Add files above to begin. Each file converts to its own `.parquet`.")
        return

    if st.button("Convert", type="primary", key="conv_go"):
        results, zip_buf = [], io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                try:
                    df = _read_tabular(f, sep=sep)
                    if infer:
                        df = _infer_types(df)
                    blob = _to_parquet_bytes(df, compression)
                    out_name = os.path.splitext(f.name)[0] + ".parquet"
                    zf.writestr(out_name, blob)
                    results.append({
                        "File": f.name, "Rows": len(df), "Columns": df.shape[1],
                        "Parquet bytes": len(blob), "Status": "OK",
                    })
                    st.session_state.setdefault("conv_out", {})[out_name] = blob
                except Exception as exc:
                    results.append({"File": f.name, "Rows": "—", "Columns": "—",
                                    "Parquet bytes": "—", "Status": f"FAILED: {exc}"})
        st.session_state["conv_zip"] = zip_buf.getvalue()
        st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)

    if st.session_state.get("conv_out"):
        st.markdown("**Downloads**")
        for name, blob in st.session_state["conv_out"].items():
            st.download_button(f"⬇ {name}", blob, file_name=name,
                               mime="application/octet-stream", key=f"dl_{name}")
        if len(st.session_state["conv_out"]) > 1 and st.session_state.get("conv_zip"):
            st.download_button("⬇ All as .zip", st.session_state["conv_zip"],
                               file_name="parquet_output.zip", mime="application/zip",
                               key="dl_zip_conv")


# ══════════════════════════════════════════════════════════════════════════════
# module: Viewer
# ══════════════════════════════════════════════════════════════════════════════
def _page_viewer():
    st.subheader("Viewer  ·  inspect a Parquet file")
    f = st.file_uploader("Upload a .parquet file", type=["parquet"], key="view_file")
    if not f:
        st.info("Upload a Parquet file to see its schema, statistics and rows.")
        return

    data = f.getvalue()
    meta = _parquet_meta(data)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows", meta["num_rows"] if meta["num_rows"] is not None else "—")
    m2.metric("Columns", len(meta["columns"]) or "—")
    m3.metric("Row groups", meta["num_row_groups"] if meta["num_row_groups"] is not None else "—")
    m4.metric("File size", f"{meta['size_bytes']/1024:.1f} KB")

    df = pd.read_parquet(io.BytesIO(data))

    with st.expander("Schema", expanded=True):
        if meta["columns"]:
            st.dataframe(pd.DataFrame(meta["columns"], columns=["Column", "Type"]),
                         use_container_width=True, hide_index=True)
        else:
            st.dataframe(pd.DataFrame({"Column": df.columns, "Type": df.dtypes.astype(str)}),
                         use_container_width=True, hide_index=True)
        if meta["created_by"]:
            st.caption(f"created_by: {meta['created_by']}")

    # filter + sort
    cols = list(df.columns)
    fc1, fc2, fc3 = st.columns([2, 2, 1])
    with fc1:
        fcol = st.selectbox("Filter column", ["(none)"] + cols, key="view_fcol")
    with fc2:
        fval = st.text_input("contains", key="view_fval")
    with fc3:
        scol = st.selectbox("Sort by", ["(none)"] + cols, key="view_scol")
    view = df
    if fcol != "(none)" and fval:
        view = view[view[fcol].astype(str).str.contains(fval, case=False, na=False)]
    if scol != "(none)":
        view = view.sort_values(scol)
    st.caption(f"Showing {len(view):,} of {len(df):,} rows")
    st.dataframe(view.head(1000), use_container_width=True)

    with st.expander("Column statistics"):
        st.dataframe(df.describe(include="all").transpose(), use_container_width=True)

    st.download_button("⬇ Export current view as CSV",
                       view.to_csv(index=False).encode("utf-8"),
                       file_name=os.path.splitext(f.name)[0] + "_view.csv",
                       mime="text/csv", key="view_dl")


# ══════════════════════════════════════════════════════════════════════════════
# module: CSV Tools
# ══════════════════════════════════════════════════════════════════════════════
def _page_csv_tools():
    st.subheader("CSV Tools")
    tool = st.radio("Tool", ["Delimiter", "Encoding", "Merge", "Split rows", "Schema compare"],
                    horizontal=True, key="csv_tool")

    if tool == "Delimiter":
        f = st.file_uploader("CSV file", type=["csv", "txt", "tsv"], key="d_file")
        src = st.selectbox("From", ["auto", ",", ";", "|", "\\t"], key="d_from")
        dst = st.selectbox("To", [",", ";", "|", "\\t"], key="d_to")
        if f and st.button("Convert delimiter", key="d_go"):
            df = _read_tabular(f, sep=src)
            out_sep = {"\\t": "\t"}.get(dst, dst)
            st.download_button("⬇ Download",
                               df.to_csv(index=False, sep=out_sep).encode("utf-8"),
                               file_name="redelimited.csv", mime="text/csv", key="d_dl")
            st.dataframe(df.head(50), use_container_width=True)

    elif tool == "Encoding":
        f = st.file_uploader("CSV file", type=["csv", "txt"], key="e_file")
        src = st.selectbox("Read as", ["utf-8", "latin-1", "cp1252", "utf-16"], key="e_from")
        dst = st.selectbox("Write as", ["utf-8", "utf-8-sig", "latin-1", "cp1252"], key="e_to")
        if f and st.button("Re-encode", key="e_go"):
            text = f.getvalue().decode(src, errors="replace")
            st.download_button("⬇ Download", text.encode(dst, errors="replace"),
                               file_name="reencoded.csv", mime="text/csv", key="e_dl")
            st.success(f"Re-encoded {len(text):,} chars: {src} → {dst}")

    elif tool == "Merge":
        files = st.file_uploader("CSV files to stack", type=["csv", "txt"],
                                 accept_multiple_files=True, key="m_files")
        how = st.radio("Mode", ["Union columns", "Only common columns"], horizontal=True, key="m_how")
        if files and st.button("Merge", key="m_go"):
            frames = [_read_tabular(f, sep="auto").assign(_source=f.name) for f in files]
            join = "outer" if how == "Union columns" else "inner"
            merged = pd.concat(frames, ignore_index=True, join=join)
            st.download_button("⬇ Download merged.csv",
                               merged.to_csv(index=False).encode("utf-8"),
                               file_name="merged.csv", mime="text/csv", key="m_dl")
            st.caption(f"{len(merged):,} rows × {merged.shape[1]} cols")
            st.dataframe(merged.head(50), use_container_width=True)

    elif tool == "Split rows":
        f = st.file_uploader("CSV file", type=["csv", "txt"], key="s_file")
        n = st.number_input("Rows per chunk", min_value=1, value=1000, step=100, key="s_n")
        if f and st.button("Split", key="s_go"):
            df = _read_tabular(f, sep="auto")
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for i in range(0, len(df), int(n)):
                    part = df.iloc[i:i + int(n)]
                    zf.writestr(f"part_{i//int(n)+1:03d}.csv", part.to_csv(index=False))
            st.download_button("⬇ Download parts.zip", zip_buf.getvalue(),
                               file_name="split_parts.zip", mime="application/zip", key="s_dl")
            st.success(f"Split {len(df):,} rows into {(len(df)-1)//int(n)+1} files")

    else:  # Schema compare
        c1, c2 = st.columns(2)
        fa = c1.file_uploader("File A", type=["csv", "txt", "parquet"], key="sc_a")
        fb = c2.file_uploader("File B", type=["csv", "txt", "parquet"], key="sc_b")
        if fa and fb and st.button("Compare", key="sc_go"):
            def _cols(f):
                if f.name.lower().endswith(".parquet"):
                    d = pd.read_parquet(io.BytesIO(f.getvalue()))
                else:
                    d = _read_tabular(f, sep="auto")
                return {c: str(t) for c, t in zip(d.columns, d.dtypes)}
            a, b = _cols(fa), _cols(fb)
            rows = []
            for col in sorted(set(a) | set(b)):
                ta, tb = a.get(col, "—"), b.get(col, "—")
                if col not in a:
                    verdict = "only in B"
                elif col not in b:
                    verdict = "only in A"
                elif ta != tb:
                    verdict = "type differs"
                else:
                    verdict = "match"
                rows.append({"Column": col, "A": ta, "B": tb, "Result": verdict})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# module: Analytics (DuckDB SQL)
# ══════════════════════════════════════════════════════════════════════════════
def _page_analytics():
    st.subheader("Analytics  ·  DuckDB SQL")
    if not _HAS_DUCK:
        st.error("`duckdb` is not installed. Add `duckdb` to requirements.txt to enable this tab.")
        return

    files = st.file_uploader("Load parquet / csv as SQL tables",
                             type=["parquet", "csv"], accept_multiple_files=True,
                             key="an_files")
    con = duckdb.connect(database=":memory:")
    registered = []
    for f in files or []:
        tbl = os.path.splitext(os.path.basename(f.name))[0]
        tbl = "".join(ch if ch.isalnum() else "_" for ch in tbl)
        if f.name.lower().endswith(".parquet"):
            df = pd.read_parquet(io.BytesIO(f.getvalue()))
        else:
            df = _read_tabular(f, sep="auto")
        con.register(tbl, df)
        registered.append((tbl, df.shape))
    if registered:
        st.caption("Tables: " + ", ".join(f"`{t}` {s}" for t, s in registered))

    default_q = f"SELECT * FROM {registered[0][0]} LIMIT 100" if registered else "SELECT 42 AS answer"
    sql = st.text_area("SQL", value=st.session_state.get("an_sql", default_q), height=120, key="an_sql_box")

    if st.button("Run", type="primary", key="an_go"):
        try:
            res = con.execute(sql).fetch_df()
            st.session_state["an_sql"] = sql
            st.session_state.setdefault("an_hist", []).insert(
                0, {"ts": datetime.now().strftime("%H:%M:%S"), "sql": sql, "rows": len(res)})
            st.dataframe(res, use_container_width=True)
            st.download_button("⬇ Result CSV", res.to_csv(index=False).encode("utf-8"),
                               file_name="query_result.csv", mime="text/csv", key="an_dl")
        except Exception as exc:
            st.error(f"Query failed: {exc}")

    if st.session_state.get("an_hist"):
        with st.expander("Query history"):
            st.dataframe(pd.DataFrame(st.session_state["an_hist"]),
                         use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# entry point
# ══════════════════════════════════════════════════════════════════════════════
def _header():
    """Shared-theme hero header when available, plain fallback otherwise —
    keeps this module runnable both inside the hub and stand-alone."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from _pages.theme import page_header, footer
    except Exception:
        st.title("🗄️ Uzumaki Parquet Tool")
        st.caption("CSV/Excel ↔ Parquet · Viewer · CSV Tools · DuckDB Analytics")
        return lambda: None

    page_header(
        "🗄️", "Uzumaki Parquet Tool",
        "CSV/Excel ↔ Parquet conversion, a schema/row-group viewer, CSV utilities, "
        "and DuckDB SQL analytics — all in one place.",
        badges=["Convert", "Viewer", "CSV Tools", "Analytics"],
    )
    return footer


def render():
    """Mountable entry — called by the hub or by the stand-alone runner."""
    render_footer = _header()
    if not _HAS_ARROW:
        st.warning("`pyarrow` not found — parquet metadata is limited. Add `pyarrow` to requirements.txt.")

    tab_convert, tab_viewer, tab_csv, tab_analytics = st.tabs(
        ["🔄 Convert", "👁️ Viewer", "🧰 CSV Tools", "📊 Analytics"]
    )
    with tab_convert:
        _page_convert()
    with tab_viewer:
        _page_viewer()
    with tab_csv:
        _page_csv_tools()
    with tab_analytics:
        _page_analytics()

    render_footer()


if __name__ == "__main__":
    st.set_page_config(page_title="Uzumaki Parquet Tool", page_icon="🗄️", layout="wide")
    render()
