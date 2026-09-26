"""
EAD Consolidator — standalone tool: upload one or more EAD Files exports,
map columns to the canonical EAD schema, and download one consolidated
file (CSV / Excel / Parquet).

Deliberately separate from the full Loan Analytics app: no login, no
engagements, nothing persisted beyond your download -- everything happens
in this one browser session, same philosophy as Parquet Tool (which this
replaces). It does reuse two things from Loan Analytics so both tools stay
consistent: the canonical EAD Files schema (column aliases) and the
System -> Product Type lookup that tags each row's Product Helper (managed
at Loan Analytics -> Settings -- adding a mapping there is immediately
picked up here too, since it's the same persisted table).

Exposes `render()` so the unified hub can mount it as a page, and also
runs stand-alone via `streamlit run ead_consolidator.py`.
"""

from __future__ import annotations

import io
import os
import sys
import zipfile
from datetime import datetime, timezone

import polars as pl
import streamlit as st

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "loans_tool", "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fcmr_core.catalog import store as loan_store  # noqa: E402
from fcmr_core.schemas.loader import (  # noqa: E402
    get_canonical_fields,
    get_schema,
    resolve_column_renames,
)

REPORT_TYPE = "ead_files"
SYSTEM_CANONICAL = "system"


# ══════════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════════
def _read_csv(csv_bytes: bytes) -> pl.DataFrame:
    return pl.read_csv(csv_bytes, infer_schema_length=10000, ignore_errors=True)


def _expand_named_bytes(named_bytes: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Same expansion as _expand_uploads, but operating on plain
    (filename, bytes) pairs instead of Streamlit UploadedFile objects --
    used once the raw bytes have already been pulled out of the
    file_uploader widget and into our own session_state (see render())."""
    expanded: list[tuple[str, bytes]] = []
    for name, data in named_bytes:
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".csv"):
                        continue
                    expanded.append((os.path.basename(info.filename), zf.read(info)))
        else:
            expanded.append((name, data))
    return expanded


def _expand_uploads(uploaded_files) -> list[tuple[str, bytes]]:
    """Flatten the raw file_uploader result into (filename, csv_bytes)
    pairs -- a .zip is extracted in place (every .csv inside it, at any
    depth, becomes one entry) so a whole folder can be uploaded at once by
    zipping it first. Streamlit's file_uploader has no native folder/
    directory picker, so this is the practical equivalent."""
    return _expand_named_bytes([(u.name, u.getvalue()) for u in uploaded_files])


def _suggested_mapping(raw_headers: list[str]) -> dict[str, str]:
    """canonical -> raw_header, for pre-filling the mapping selectboxes."""
    schema = get_schema(REPORT_TYPE)
    if not schema:
        return {}
    return schema.best_raw_for_canonical(raw_headers)


def _unmapped_system_values(frames: list[pl.DataFrame], raw_system_header: str) -> list[str]:
    known = loan_store.get_system_type_map()
    values: set[str] = set()
    for df in frames:
        if raw_system_header in df.columns:
            values.update(v for v in df[raw_system_header].drop_nulls().unique().to_list())
    return sorted(v for v in values if v not in known)


def _consolidate(frames: list[pl.DataFrame], filenames: list[str], mapping: dict[str, str]) -> pl.DataFrame:
    """mapping is {raw_header: canonical}, built from the first file and
    applied to every file -- a later file missing/renaming a column just
    keeps that column under its own raw name rather than erroring, since
    real month-to-month EAD exports share the same layout in practice."""
    tagged = []
    for df, filename in zip(frames, filenames):
        rename = {raw: canonical for raw, canonical in mapping.items() if raw in df.columns}
        # Resolve to a collision-free rename covering every column in this
        # file, not just the ones `mapping` touches -- a rename target
        # that happens to already be a *different*, untouched column's own
        # name would otherwise crash DataFrame.rename ("column ... is
        # duplicate"). See resolve_column_renames for the resolution
        # policy (explicit renames win; the loser gets a numeric suffix,
        # never silently dropped or overwritten).
        final_names = resolve_column_renames(df.columns, rename)
        effective_rename = {raw: final for raw, final in final_names.items() if final != raw}
        if effective_rename:
            df = df.rename(effective_rename)
        df = df.with_columns(pl.lit(filename).alias("_source_file"))
        tagged.append(df)

    consolidated = pl.concat(tagged, how="diagonal_relaxed")
    if SYSTEM_CANONICAL in consolidated.columns:
        type_map = loan_store.get_system_type_map()
        consolidated = consolidated.with_columns(
            pl.col(SYSTEM_CANONICAL).replace_strict(type_map, default=None).alias("product_helper")
        )
    return consolidated


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


# Excel hard-caps at 1,048,576 rows per sheet (1 header + 1,048,575 data rows).
EXCEL_ROW_LIMIT = 1_048_575


def _build_downloads(consolidated: pl.DataFrame) -> dict[str, dict]:
    """Builds the bytes for each download format independently, isolating
    failures so one format can never hide the others -- previously CSV,
    Excel, and Parquet were built back-to-back in one pass, so a
    consolidation past Excel's 1,048,576-row-per-sheet limit made
    to_excel() raise and killed the whole script before it ever reached
    the CSV/Parquet buttons, leaving *no* download option visible at all
    even though CSV/Parquet were perfectly fine.

    Returns {"csv": {...}, "excel": {...}, "parquet": {...}}, each a dict
    with "data" (bytes or None), "error" (str or None), and, for excel
    only, "skipped_reason" (str or None) when the row limit was hit.
    """
    results: dict[str, dict] = {
        "csv": {"data": None, "error": None},
        "excel": {"data": None, "error": None, "skipped_reason": None},
        "parquet": {"data": None, "error": None},
    }

    try:
        results["csv"]["data"] = consolidated.write_csv().encode("utf-8")
    except Exception as exc:
        results["csv"]["error"] = str(exc)

    if len(consolidated) > EXCEL_ROW_LIMIT:
        results["excel"]["skipped_reason"] = (
            f"Too many rows for Excel ({len(consolidated):,} > {EXCEL_ROW_LIMIT:,} row limit "
            "per sheet) — use CSV or Parquet instead."
        )
    else:
        try:
            excel_buf = io.BytesIO()
            consolidated.to_pandas().to_excel(excel_buf, index=False, engine="xlsxwriter")
            results["excel"]["data"] = excel_buf.getvalue()
        except Exception as exc:
            results["excel"]["error"] = str(exc)

    try:
        parquet_buf = io.BytesIO()
        consolidated.write_parquet(parquet_buf)
        results["parquet"]["data"] = parquet_buf.getvalue()
    except Exception as exc:
        results["parquet"]["error"] = str(exc)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════
def _reset():
    for key in ("ec_files", "ec_raw_uploads", "ec_mapping_confirmed", "ec_user_mapping", "ec_consolidated"):
        st.session_state.pop(key, None)


def render():
    render_footer = _header()

    # Idempotent (CREATE TABLE IF NOT EXISTS + seed-if-missing) -- needed
    # here because this tool can be opened before Loan Analytics itself
    # ever has been, and it shares that same catalog.duckdb for the
    # System -> Product Type lookup.
    loan_store.init_catalog()

    # The uploader's key is rotated every time we capture a fresh batch
    # (below) so its *previous* Python-side widget instance is retired
    # rather than kept alive across further reruns. This matters because
    # Streamlit deep-copies a widget's buffered value on every single
    # script rerun (Confirm Mapping, saving a System Type mapping, Start
    # Over, ...) purely for its own change-detection bookkeeping -- for a
    # large multi-file batch (many GB of EAD exports) that duplicates the
    # whole payload on every rerun and was crashing the packaged app with
    # a real MemoryError, entirely inside Streamlit's file_uploader
    # registration, before any of our own code ran. Pulling the bytes out
    # once into a plain session_state key (not a widget, so never
    # deep-copied by this mechanism) and swapping in a fresh, empty
    # uploader avoids that repeated duplication.
    uploader_gen = st.session_state.get("ec_uploader_gen", 0)
    new_uploads = st.file_uploader(
        "Upload EAD Files (CSV, or a .zip of a whole folder of them)",
        type=["csv", "zip"],
        accept_multiple_files=True,
        key=f"ec_uploader_{uploader_gen}",
    )
    st.caption("Up to 2 GB per file. No native folder picker in the browser -- zip the folder and upload that instead.")

    if new_uploads:
        st.session_state["ec_raw_uploads"] = [(f.name, f.getvalue()) for f in new_uploads]
        st.session_state["ec_uploader_gen"] = uploader_gen + 1
        for key in ("ec_mapping_confirmed", "ec_user_mapping", "ec_consolidated"):
            st.session_state.pop(key, None)
        st.rerun()

    raw_uploads = st.session_state.get("ec_raw_uploads")
    if not raw_uploads:
        st.info("Upload one or more EAD Files exports to get started.")
        render_footer()
        return

    expanded = _expand_named_bytes(raw_uploads)
    if not expanded:
        st.error("No CSV files found (an uploaded .zip had none inside it).")
        render_footer()
        return

    filenames = [name for name, _ in expanded]
    frames = [_read_csv(csv_bytes) for _, csv_bytes in expanded]

    st.caption(f"{len(frames)} file(s) loaded — mapping is based on **{filenames[0]}** and applied to all.")

    canonical_fields = get_canonical_fields(REPORT_TYPE)
    raw_headers = frames[0].columns
    suggested = _suggested_mapping(raw_headers)

    with st.form("ec_mapping_form"):
        st.markdown("#### Map columns")
        user_mapping: dict[str, str] = {}
        for spec in canonical_fields:
            default_raw = suggested.get(spec.canonical, "— Skip —")
            options = ["— Skip —"] + raw_headers
            index = options.index(default_raw) if default_raw in options else 0
            label = f"{spec.canonical}{' *' if spec.required else ''}"
            label_col, field_col = st.columns([1, 2])
            with label_col:
                st.markdown(f"<div style='padding-top:8px;'>{label}</div>", unsafe_allow_html=True)
            with field_col:
                choice = st.selectbox(
                    label, options, index=index, key=f"ec_map_{spec.canonical}",
                    label_visibility="collapsed",
                )
            if choice != "— Skip —":
                user_mapping[choice] = spec.canonical
        submitted = st.form_submit_button("Confirm Mapping")

    if submitted:
        st.session_state["ec_user_mapping"] = user_mapping
        st.session_state["ec_mapping_confirmed"] = True
        st.session_state.pop("ec_consolidated", None)

    if not st.session_state.get("ec_mapping_confirmed"):
        render_footer()
        return

    user_mapping = st.session_state["ec_user_mapping"]
    raw_system_header = next((h for h, c in user_mapping.items() if c == SYSTEM_CANONICAL), None)

    if raw_system_header:
        unmapped = _unmapped_system_values(frames, raw_system_header)
        if unmapped:
            st.warning(
                f"{len(unmapped)} System value(s) aren't mapped to a Product Type yet. "
                "Add them below (saved for next time too)."
            )
            with st.form("ec_system_type_form"):
                new_types = {}
                for value in unmapped:
                    new_types[value] = st.text_input(f"Product Type for **{value}**", key=f"ec_type_{value}")
                if st.form_submit_button("Save mappings and continue"):
                    for value, type_value in new_types.items():
                        if type_value.strip():
                            loan_store.set_system_type(value, type_value.strip())
                    st.rerun()
            render_footer()
            return

    if "ec_consolidated" not in st.session_state:
        with st.spinner("Consolidating…"):
            st.session_state["ec_consolidated"] = _consolidate(frames, filenames, user_mapping)

    consolidated = st.session_state["ec_consolidated"]
    st.success(f"Consolidated {len(consolidated):,} rows from {len(raw_uploads)} file(s).")
    st.dataframe(consolidated.head(50).to_pandas(), use_container_width=True)

    ts = _timestamp()
    downloads = _build_downloads(consolidated)
    col1, col2, col3 = st.columns(3)
    with col1:
        csv_result = downloads["csv"]
        if csv_result["error"]:
            st.error(f"CSV generation failed: {csv_result['error']}")
        else:
            st.download_button(
                "⬇ Download CSV",
                data=csv_result["data"],
                file_name=f"EAD_Consolidated_{ts}.csv",
                mime="text/csv",
                use_container_width=True,
            )
    with col2:
        excel_result = downloads["excel"]
        if excel_result["skipped_reason"]:
            st.warning(excel_result["skipped_reason"])
        elif excel_result["error"]:
            st.error(f"Excel generation failed: {excel_result['error']}")
        else:
            st.download_button(
                "⬇ Download Excel",
                data=excel_result["data"],
                file_name=f"EAD_Consolidated_{ts}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
    with col3:
        parquet_result = downloads["parquet"]
        if parquet_result["error"]:
            st.error(f"Parquet generation failed: {parquet_result['error']}")
        else:
            st.download_button(
                "⬇ Download Parquet",
                data=parquet_result["data"],
                file_name=f"EAD_Consolidated_{ts}.parquet",
                mime="application/octet-stream",
                use_container_width=True,
            )

    st.button("Start over", on_click=_reset)
    render_footer()


def _header():
    """Shared-theme hero header when available, plain fallback otherwise --
    keeps this module runnable both inside the hub and stand-alone."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from _pages.theme import footer, page_header
    except Exception:
        st.title("📥 EAD Consolidator")
        st.caption("Upload, map, and consolidate EAD Files into one download.")
        return lambda: None

    page_header(
        "📥", "EAD Consolidator",
        "Upload EAD Files exports, map columns to the canonical schema, and download "
        "one consolidated file — CSV, Excel, or Parquet.",
        badges=["Upload", "Map", "Consolidate", "Download"],
    )
    return footer


if __name__ == "__main__":
    st.set_page_config(page_title="EAD Consolidator", page_icon="📥", layout="wide")
    render()
