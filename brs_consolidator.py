"""
BRS Consolidator — standalone tool: upload a batch of Bank Reconciliation
Statement files (one folder per product, zipped -- or loose Excel/CSV
files for a single product), map columns to the canonical BRS schema, and
download two consolidated files -- Disbursement and Collection -- each
tagged with Product and traceable back to its source file.

Deliberately separate from the full Loan Analytics app: no login, no
engagements, nothing persisted beyond your download -- same philosophy as
EAD Consolidator. Each product folder is expected to hold two files (one
Disbursement, one Collection), classified by filename; only the sheet
named "Transaction Details 1" (case-insensitive, whitespace-trimmed) is
read out of each Excel file, a CSV is read as-is.

Exposes `render()` so the unified hub can mount it as a page, and also
runs stand-alone via `streamlit run brs_consolidator.py`.
"""

from __future__ import annotations

import io
import os
import sys
import zipfile
from datetime import datetime, timezone

import openpyxl
import polars as pl
import streamlit as st

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "loans_tool", "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fcmr_core.catalog import store as loan_store  # noqa: E402
from fcmr_core.schemas.loader import ColumnSpec, SchemaMap, resolve_column_renames  # noqa: E402

_TARGET_SHEET = "transaction details 1"
_EXCEL_EXTS = (".xlsx", ".xls")
_ALL_EXTS = (".csv",) + _EXCEL_EXTS

# Built directly as a SchemaMap rather than a fcmr_core/schemas/*.yaml file:
# a yaml there is auto-discovered by available_report_types() and would
# leak "BRS" into the main Loan Analytics app's own upload dropdown and
# Analytics hub -- this tool is standalone, the user asked for a
# consolidator, not a new report type in that app's ingest pipeline. The
# 15-column canonical schema is the exact list given by the user, kept
# local to this module instead.
_BRS_COLUMNS = [
    ColumnSpec("category", ["CATEGORY", "Category", "category"], False, "str"),
    ColumnSpec(
        "reco_doc_no",
        ["Reco / Doc No.", "Reco/Doc No.", "RECO / DOC NO.", "Reco Doc No", "reco_doc_no"],
        False,
        "str",
    ),
    ColumnSpec(
        "agreement_no",
        ["AGREEMENT NO", "Agreement No", "AGREEMENT NO.", "agreement_no", "AgreementNo"],
        True,
        "str",
    ),
    ColumnSpec("ref1", ["Ref1", "REF1", "ref1"], False, "str"),
    ColumnSpec("ref2", ["Ref2", "REF2", "ref2"], False, "str"),
    ColumnSpec("ref3", ["Ref3", "REF3", "ref3"], False, "str"),
    ColumnSpec("txn_date", ["DATE", "Date", "date"], False, "str"),
    ColumnSpec("amount", ["AMOUNT", "Amount", "amount"], False, "float"),
    ColumnSpec("bank_name", ["BANK NAME", "Bank Name", "bank_name"], False, "str"),
    ColumnSpec("narration", ["NARRATION", "Narration", "narration"], False, "str"),
    ColumnSpec("group_glid", ["GROUPGLID", "GROUP GLID", "group_glid", "GroupGLID"], False, "str"),
    ColumnSpec("ageing", ["Ageing", "AGEING", "ageing"], False, "str"),
    ColumnSpec("ageing_bucket", ["Ageing Bucket", "AGEING BUCKET", "ageing_bucket"], False, "str"),
    ColumnSpec("remarks", ["Remarks", "REMARKS", "remarks"], False, "str"),
    ColumnSpec("clearance_date", ["Clearance Date", "CLEARANCE DATE", "clearance_date"], False, "str"),
]
_BRS_SCHEMA = SchemaMap(report_type="brs", columns=_BRS_COLUMNS)


# ══════════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════════
def _classify_kind(filename: str) -> str | None:
    """"Disbursement" / "Collection" from the filename, or None if neither
    word appears (case-insensitive) -- the file needs manual attention."""
    name = filename.lower()
    if "disbursement" in name or "disbursal" in name:
        return "Disbursement"
    if "collection" in name:
        return "Collection"
    return None


def _read_sheet(filename: str, data: bytes) -> tuple[pl.DataFrame | None, str | None]:
    """Reads the "Transaction Details 1" sheet from an Excel file, or the
    whole file for a CSV. Returns (df, error) -- exactly one is set."""
    lower = filename.lower()
    if lower.endswith(".csv"):
        try:
            return pl.read_csv(data, infer_schema_length=10000, ignore_errors=True), None
        except Exception as exc:
            return None, f"Could not read {filename} as CSV: {exc}"

    if lower.endswith(_EXCEL_EXTS):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            sheet_names = list(wb.sheetnames)
            wb.close()
        except Exception as exc:
            return None, f"Could not open {filename}: {exc}"

        match = next((s for s in sheet_names if s.strip().lower() == _TARGET_SHEET), None)
        if not match:
            return None, f'{filename}: no "Transaction Details 1" sheet found (has: {", ".join(sheet_names)})'
        try:
            df = pl.read_excel(io.BytesIO(data), sheet_name=match, engine="calamine")
            return df, None
        except Exception as exc:
            return None, f"Could not read sheet '{match}' from {filename}: {exc}"

    return None, f"{filename}: unsupported file type (expected .csv, .xlsx, or .xls)"


def _expand_zip(data: bytes) -> list[tuple[str | None, str, bytes]]:
    """Extracts every .csv/.xlsx/.xls entry from a zip, returning
    (product, filename, bytes) tuples -- product is the entry's top-level
    folder name, or None for a file sitting at the zip's root."""
    expanded: list[tuple[str | None, str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(_ALL_EXTS):
                continue
            parts = info.filename.replace("\\", "/").split("/")
            product = parts[0] if len(parts) > 1 else None
            expanded.append((product, os.path.basename(info.filename), zf.read(info)))
    return expanded


def _expand_named_bytes(named_bytes: list[tuple[str, bytes]]) -> list[tuple[str | None, str, bytes]]:
    """Expands zips (keeping a per-entry product tag from the top-level
    folder), passes bare files through with product=None -- a bare file,
    or a file sitting at the zip's own root with no subfolder, has no
    product to infer and needs a manual name (see render())."""
    expanded: list[tuple[str | None, str, bytes]] = []
    for name, data in named_bytes:
        if name.lower().endswith(".zip"):
            expanded.extend(_expand_zip(data))
        else:
            expanded.append((None, name, data))
    return expanded


def _suggested_mapping(raw_headers: list[str]) -> dict[str, str]:
    return _BRS_SCHEMA.best_raw_for_canonical(raw_headers)


def _consolidate(entries: list[dict], mapping: dict[str, str]) -> pl.DataFrame:
    """entries: [{"product", "filename", "df"}, ...], already filtered to
    one kind (Disbursement or Collection) by the caller. Renames each
    entry's df using the shared mapping (collision-free per file, same
    policy as EAD Consolidator's _consolidate), tags Product/_source_file,
    and stacks."""
    tagged = []
    for entry in entries:
        df = entry["df"]
        rename = {raw: canonical for raw, canonical in mapping.items() if raw in df.columns}
        final_names = resolve_column_renames(df.columns, rename)
        effective_rename = {raw: final for raw, final in final_names.items() if final != raw}
        if effective_rename:
            df = df.rename(effective_rename)
        df = df.with_columns(
            [
                pl.lit(entry["product"]).alias("product"),
                pl.lit(entry["filename"]).alias("_source_file"),
            ]
        )
        tagged.append(df)
    return pl.concat(tagged, how="diagonal_relaxed")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


# Excel hard-caps at 1,048,576 rows per sheet (1 header + 1,048,575 data rows).
EXCEL_ROW_LIMIT = 1_048_575


def _build_downloads(consolidated: pl.DataFrame) -> dict[str, dict]:
    """Builds each format independently so one failing (e.g. Excel past
    its row limit) can never hide the others -- same fix as EAD
    Consolidator's _build_downloads."""
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
    for key in (
        "bc_raw_uploads",
        "bc_product_override",
        "bc_mapping_confirmed",
        "bc_user_mapping",
        "bc_disb_consolidated",
        "bc_coll_consolidated",
    ):
        st.session_state.pop(key, None)


def _render_download_section(label: str, consolidated: pl.DataFrame, key_prefix: str) -> None:
    st.markdown(f"#### {label} — {len(consolidated):,} rows, {consolidated['product'].n_unique()} product(s)")
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
                f"⬇ Download {label} CSV",
                data=csv_result["data"],
                file_name=f"BRS_{label}_{ts}.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"{key_prefix}_csv",
            )
    with col2:
        excel_result = downloads["excel"]
        if excel_result["skipped_reason"]:
            st.warning(excel_result["skipped_reason"])
        elif excel_result["error"]:
            st.error(f"Excel generation failed: {excel_result['error']}")
        else:
            st.download_button(
                f"⬇ Download {label} Excel",
                data=excel_result["data"],
                file_name=f"BRS_{label}_{ts}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"{key_prefix}_excel",
            )
    with col3:
        parquet_result = downloads["parquet"]
        if parquet_result["error"]:
            st.error(f"Parquet generation failed: {parquet_result['error']}")
        else:
            st.download_button(
                f"⬇ Download {label} Parquet",
                data=parquet_result["data"],
                file_name=f"BRS_{label}_{ts}.parquet",
                mime="application/octet-stream",
                use_container_width=True,
                key=f"{key_prefix}_parquet",
            )


def render():
    render_footer = _header()

    # Idempotent (CREATE TABLE IF NOT EXISTS) -- needed here because this
    # tool can be opened before Loan Analytics itself ever has been, and
    # get_schema()'s fuzzy-matching reads settings from the same
    # catalog.duckdb (e.g. the fuzzy_match_threshold setting).
    loan_store.init_catalog()

    # Same widget-retirement pattern as EAD Consolidator: capture the
    # uploaded bytes into plain session_state once, then rotate the
    # uploader's key, so a large batch doesn't get re-deep-copied by
    # Streamlit's own widget bookkeeping on every later rerun (a real
    # MemoryError found and fixed there).
    uploader_gen = st.session_state.get("bc_uploader_gen", 0)
    new_uploads = st.file_uploader(
        "Upload BRS files (CSV/Excel, or a .zip with one folder per product)",
        type=["csv", "xlsx", "xls", "zip"],
        accept_multiple_files=True,
        key=f"bc_uploader_{uploader_gen}",
    )
    st.caption(
        "Zip a folder per product (each with its Disbursement and Collection files) to consolidate several "
        "products at once -- Streamlit has no native folder picker. Loose files without a folder are all "
        "treated as one product; you'll be asked to name it below."
    )

    if new_uploads:
        st.session_state["bc_raw_uploads"] = [(f.name, f.getvalue()) for f in new_uploads]
        st.session_state["bc_uploader_gen"] = uploader_gen + 1
        for key in ("bc_product_override", "bc_mapping_confirmed", "bc_user_mapping", "bc_disb_consolidated", "bc_coll_consolidated"):
            st.session_state.pop(key, None)
        st.rerun()

    raw_uploads = st.session_state.get("bc_raw_uploads")
    if not raw_uploads:
        st.info("Upload one or more BRS exports to get started.")
        render_footer()
        return

    expanded = _expand_named_bytes(raw_uploads)
    if not expanded:
        st.error("No CSV/Excel files found (an uploaded .zip had none inside it).")
        render_footer()
        return

    # Files with no folder (bare uploads, or sitting at the zip root) need
    # a manual product name -- collected once, applied to all of them.
    needs_product = any(product is None for product, _, _ in expanded)
    if needs_product and "bc_product_override" not in st.session_state:
        with st.form("bc_product_form"):
            st.markdown("#### Name the product for files without a folder")
            unnamed = [filename for product, filename, _ in expanded if product is None]
            st.caption(", ".join(unnamed))
            name = st.text_input("Product name")
            if st.form_submit_button("Continue") and name.strip():
                st.session_state["bc_product_override"] = name.strip()
                st.rerun()
        render_footer()
        return

    override = st.session_state.get("bc_product_override")
    resolved = [(product or override, filename, data) for product, filename, data in expanded]

    # Read each file's target content and classify it, collecting errors
    # rather than failing the whole batch on one bad file.
    entries: list[dict] = []
    errors: list[str] = []
    unrecognized: list[str] = []
    for product, filename, data in resolved:
        kind = _classify_kind(filename)
        if kind is None:
            unrecognized.append(filename)
            continue
        df, error = _read_sheet(filename, data)
        if error:
            errors.append(error)
            continue
        entries.append({"product": product, "filename": filename, "kind": kind, "df": df})

    if unrecognized:
        st.warning(
            f"{len(unrecognized)} file(s) skipped -- filename doesn't contain \"Disbursement\" or "
            f"\"Collection\": {', '.join(unrecognized)}"
        )
    if errors:
        st.error("\n\n".join(errors))
    if not entries:
        st.error("No usable Disbursement/Collection files found.")
        render_footer()
        return

    st.caption(f"{len(entries)} file(s) loaded across {len({e['product'] for e in entries})} product(s).")
    st.dataframe(
        pl.DataFrame(
            {
                "Product": [e["product"] for e in entries],
                "File": [e["filename"] for e in entries],
                "Kind": [e["kind"] for e in entries],
                "Rows": [len(e["df"]) for e in entries],
            }
        ).to_pandas(),
        use_container_width=True,
    )

    canonical_fields = _BRS_COLUMNS
    raw_headers = entries[0]["df"].columns
    suggested = _suggested_mapping(raw_headers)

    with st.form("bc_mapping_form"):
        st.markdown("#### Map columns")
        st.caption(f"Based on **{entries[0]['filename']}**, applied to every file.")
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
                    label, options, index=index, key=f"bc_map_{spec.canonical}",
                    label_visibility="collapsed",
                )
            if choice != "— Skip —":
                user_mapping[choice] = spec.canonical
        submitted = st.form_submit_button("Confirm Mapping")

    if submitted:
        st.session_state["bc_user_mapping"] = user_mapping
        st.session_state["bc_mapping_confirmed"] = True
        st.session_state.pop("bc_disb_consolidated", None)
        st.session_state.pop("bc_coll_consolidated", None)

    if not st.session_state.get("bc_mapping_confirmed"):
        render_footer()
        return

    user_mapping = st.session_state["bc_user_mapping"]
    disb_entries = [e for e in entries if e["kind"] == "Disbursement"]
    coll_entries = [e for e in entries if e["kind"] == "Collection"]

    if "bc_disb_consolidated" not in st.session_state and disb_entries:
        with st.spinner("Consolidating Disbursement…"):
            st.session_state["bc_disb_consolidated"] = _consolidate(disb_entries, user_mapping)
    if "bc_coll_consolidated" not in st.session_state and coll_entries:
        with st.spinner("Consolidating Collection…"):
            st.session_state["bc_coll_consolidated"] = _consolidate(coll_entries, user_mapping)

    disb_consolidated = st.session_state.get("bc_disb_consolidated")
    coll_consolidated = st.session_state.get("bc_coll_consolidated")

    if disb_consolidated is not None:
        _render_download_section("Disbursement", disb_consolidated, "bc_disb")
    else:
        st.info("No Disbursement files to consolidate.")

    if coll_consolidated is not None:
        _render_download_section("Collection", coll_consolidated, "bc_coll")
    else:
        st.info("No Collection files to consolidate.")

    st.button("Start over", on_click=_reset)
    render_footer()


def _header():
    """Shared-theme hero header when available, plain fallback otherwise --
    keeps this module runnable both inside the hub and stand-alone."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from _pages.theme import footer, page_header
    except Exception:
        st.title("🏦 BRS Consolidator")
        st.caption("Upload, map, and consolidate Bank Reconciliation Statement files by product.")
        return lambda: None

    page_header(
        "🏦", "BRS Consolidator",
        "Upload Bank Reconciliation Statement files (one folder per product), map columns to the canonical "
        "schema, and download consolidated Disbursement and Collection files — CSV, Excel, or Parquet.",
        badges=["Upload", "Map", "Consolidate", "Download"],
    )
    return footer


if __name__ == "__main__":
    st.set_page_config(page_title="BRS Consolidator", page_icon="🏦", layout="wide")
    render()
