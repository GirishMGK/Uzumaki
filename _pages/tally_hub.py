"""Hub page: Tally — one nav entry for every Tally activity.

Previously six separate sidebar pages (Tally extraction tool, Sales &
Purchase Register, GST Summary, TDS Summary, Bank Reconciliation, Inventory
Closing Stock), each with its own "Tally host / port / Test Connection /
Company" block -- meaning connecting to Tally once didn't carry over to the
next activity; every page made you reconnect from scratch. Consolidated per
a direct user request: one icon, connect once, then every activity below
reuses that same connection.

Each activity keeps its own Upload / Live sub-tabs (uploading a JSON/XML
export doesn't need a Tally connection at all, so that path is unaffected);
only the Live sub-tabs now share the one connection picker at the top of
this page instead of each rendering (and requiring) their own.

All business logic below is unchanged from the six standalone pages this
replaces -- ported as-is, not rewritten -- see each activity's own comments
for the same caveats/verification notes the original pages carried.
"""
from __future__ import annotations

import datetime
import io
import os
import sys
import tempfile

import pandas as pd
import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tally_tool"))
sys.path.insert(0, os.path.join(_ROOT, "je_audit_tool"))

from _pages.theme import page_header, footer
from _pages.tally_common import (
    render_connection_picker, render_setup_help, _current_fy_start, extract_any_with_progress,
)

from extract_ledgers import ensure_utf8, build_tables, write_output
from common.statutory_extractors import detect_statutory_type, extract_gstr1, extract_gstr3b, extract_tds, read_pdf_text
from reports.sales_purchase_register import build_hsn_summary, extract_register_from_export
from reports.gst_summary import build_gst_summary, build_month_pivot, build_unclassified
from reports.gst_recon import compute_recon_3way, resolve_gstin
from reports.tds_summary import build_tds_summary
from reports.tds_recon import build_tds_recon
from reports.bank_recon import book_balance_as_of, build_brs, match_statement_to_books, normalize_statement
from reports.inventory_stock import build_negative_stock_flags, build_stock_summary, extract_stock_items_from_export
from utils.data_loader import load_single_file
import tally_connector

page_header(
    "📒", "Tally",
    "Every Tally activity in one place — connect once above, then extract ledgers, "
    "pull registers, and build GST/TDS/bank/inventory summaries below without "
    "reconnecting for each one.",
    badges=["Connect once", "6 activities", "Upload export or connect live"],
)

render_setup_help(expanded=False)
host, port, company = render_connection_picker("tally_hub")
st.divider()

_DATE_KW = dict(format="YYYY-MM-DD", min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1))


# ═════════════════════════════════════════════════════════════════════════
# Activity: Extraction (ledger-wise)
# ═════════════════════════════════════════════════════════════════════════
def _render_extraction_results(df, summary, tmpdir):
    out_path = os.path.join(tmpdir, "tally_extract.xlsx")
    write_output(df, summary, out_path, "xlsx")

    total_dr = df["Debit"].sum()
    total_cr = df["Credit"].sum()
    diff = total_dr - total_cr

    st.divider()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Ledgers", df["Ledger Name"].nunique())
    k2.metric("Transaction rows", len(df))
    k3.metric("Total Debit", f"{total_dr:,.2f}")
    k4.metric("Total Credit", f"{total_cr:,.2f}", delta=f"{diff:,.2f}" if abs(diff) >= 0.01 else None,
              delta_color="inverse")

    if abs(diff) < 0.01:
        st.success("Control total OK — Debit and Credit match to the paisa.")
    else:
        st.warning(
            f"Control total does not balance (difference {diff:,.2f}). "
            "Expected if you filtered to a subset of ledgers — otherwise, "
            "double-check the export/pull covers a full period."
        )

    st.dataframe(df.head(500), use_container_width=True, hide_index=True)
    if len(df) > 500:
        st.caption(f"Showing first 500 of {len(df):,} rows — download the workbook for the full data.")

    with open(out_path, "rb") as f:
        st.download_button(
            "⬇ Download workbook (Transactions + Ledger Summary)",
            f.read(),
            file_name="tally_extract.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="ext_download",
        )


def _render_extraction_tab():
    tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

    with tab_upload:
        with st.expander("How to export from Tally", expanded=False):
            st.markdown(
                """
1. **Gateway of Tally → Display → Day Book** (or any report/period covering everything you need)
2. **Alt+F2** → set the date range to the full period (e.g. the full financial year)
3. **F12** (Configure) → make sure narrations and full ledger-entry detail are shown
4. **Alt+E** (Export) → Format: **JSON (Data Interchange)** or **XML (Data Interchange)** → Yes to "Export All"

This single file contains every **Ledger master** and every **Voucher** with its full
ledger-entry detail — everything this tool needs, in either format.
"""
            )

        uploaded = st.file_uploader("Tally export (JSON or XML)", type=["json", "xml"], key="ext_upload_file")

        c1, c2, c3 = st.columns(3)
        with c1:
            include_cancelled_u = st.checkbox("Include cancelled/optional vouchers", value=False, key="ext_ic_upload")
        with c2:
            from_date_u = st.date_input("From date (optional)", value=None, key="ext_fd_upload", **_DATE_KW)
        with c3:
            to_date_u = st.date_input("To date (optional)", value=None, key="ext_td_upload", **_DATE_KW)

        ledger_filter_u = st.text_input(
            "Only these ledgers (optional — exact names, comma-separated)",
            placeholder="e.g. Cash, ABC Traders", key="ext_lf_upload",
        )

        if uploaded and st.button("Extract", type="primary", key="ext_extract_upload"):
            with tempfile.TemporaryDirectory() as tmpdir:
                in_path = os.path.join(tmpdir, uploaded.name or "Transactions.json")
                with open(in_path, "wb") as f:
                    f.write(uploaded.getvalue())
                try:
                    with st.spinner("Checking encoding…"):
                        utf8_path = ensure_utf8(in_path)
                    ledger_master, rows = extract_any_with_progress(utf8_path)
                    with st.spinner("Building ledger tables and running balances…"):
                        df, summary = build_tables(
                            ledger_master, rows, include_cancelled=include_cancelled_u,
                            from_date=from_date_u if isinstance(from_date_u, datetime.date) else None,
                            to_date=to_date_u if isinstance(to_date_u, datetime.date) else None,
                            ledger_filter=ledger_filter_u or None,
                        )
                except SystemExit as e:
                    st.error(str(e))
                    st.stop()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")
                    st.stop()
                _render_extraction_results(df, summary, tmpdir)

    with tab_live:
        st.caption(
            "Both dates are required for a live pull — confirmed against a real Tally instance: "
            "without an explicit date range, Tally's XML server silently returns a diagnostic "
            "summary instead of voucher data rather than an error. Defaults below cover the "
            "current financial year — a very wide range can genuinely take Tally minutes to "
            "compute and may time out; pull one financial year at a time if you need more history."
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="ext_ic_live")
        with c2:
            from_date_l = st.date_input("From date", value=_current_fy_start(), key="ext_fd_live", **_DATE_KW)
        with c3:
            to_date_l = st.date_input("To date", value=datetime.date.today(), key="ext_td_live", **_DATE_KW)

        ledger_filter_l = st.text_input(
            "Only these ledgers (optional — exact names, comma-separated)",
            placeholder="e.g. Cash, ABC Traders", key="ext_lf_live",
        )

        if st.button("Pull from Tally", type="primary", key="ext_pull_live"):
            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    with st.spinner("Pulling ledgers and vouchers from Tally…"):
                        ledger_master, rows = tally_connector.pull_from_tally(
                            host, int(port), company,
                            from_date_l if isinstance(from_date_l, datetime.date) else None,
                            to_date_l if isinstance(to_date_l, datetime.date) else None,
                        )
                    with st.spinner("Building ledger tables and running balances…"):
                        df, summary = build_tables(
                            ledger_master, rows, include_cancelled=include_cancelled_l,
                            from_date=from_date_l if isinstance(from_date_l, datetime.date) else None,
                            to_date=to_date_l if isinstance(to_date_l, datetime.date) else None,
                            ledger_filter=ledger_filter_l or None,
                        )
                except tally_connector.TallyConnectionError as e:
                    st.error(str(e))
                    st.stop()
                except SystemExit as e:
                    st.error(str(e))
                    st.stop()
                except Exception as e:
                    st.error(f"Pull failed: {e}")
                    st.stop()
                _render_extraction_results(df, summary, tmpdir)

    with st.expander("What this does"):
        st.markdown(
            """
- **Transactions sheet** — one row per ledger entry: Ledger Name, Ledger Group, Date,
  Voucher Type, Voucher No, Reference, Party Ledger, Narration, Debit, Credit,
  Opening Balance, Running Balance (+ Dr/Cr label), Bill Reference, Cost Centre
  (blank if the company doesn't use cost centres), Voucher GUID, Master ID.
- **Ledger Summary sheet** — one row per ledger: Group, Opening Balance, Total Debit,
  Total Credit, Closing Balance, Transaction Count — use this to tie out against your
  trial balance.
- **Debit/Credit** is taken from the **sign of Tally's `amount` field**, not the
  `isdeemedpositive` flag — the flag was found unreliable on some statutory/duty ledger
  entries (e.g. TDS lines on vouchers migrated from an older Tally version).
- Cancelled and optional (memo) vouchers are excluded by default, matching what Tally
  itself shows in a normal ledger view.

**Command line** (for scripting/large batches):
```bash
python tally_tool/extract_ledgers.py --input "Transactions.json" --output "ledgers_output.xlsx"
```
"""
        )


# ═════════════════════════════════════════════════════════════════════════
# Activity: Sales & Purchase Register
# ═════════════════════════════════════════════════════════════════════════
_GST_COLS = ["CGST", "SGST", "IGST", "UTGST", "CESS"]


def _render_register_results(df_reg: pd.DataFrame, register_type: str) -> None:
    if df_reg.empty:
        st.warning(f"No {register_type} vouchers found in this date range/company.")
        return

    unique_vouchers = df_reg.drop_duplicates(subset=["Voucher No", "Voucher GUID"])
    total_value = unique_vouchers["Voucher Total"].sum()
    has_gst = any(c in df_reg.columns and df_reg[c].abs().sum() > 0 for c in _GST_COLS)

    st.divider()
    k1, k2, k3 = st.columns(3)
    k1.metric("Vouchers", len(unique_vouchers))
    k2.metric("Item lines", len(df_reg))
    k3.metric(f"Total {register_type} value", f"{total_value:,.2f}")
    if not has_gst:
        st.caption(
            "GST breakup columns are all-zero — either this pull's ledger master had no "
            "recognisable GST ledgers, or the vouchers genuinely carry no tax (e.g. exempt supplies)."
        )

    hsn_summary = build_hsn_summary(df_reg)
    tab_detail, tab_hsn = st.tabs(["📋 Register", f"🏷️ HSN Summary ({len(hsn_summary)})"])
    with tab_detail:
        st.dataframe(df_reg.head(500), use_container_width=True, hide_index=True)
        if len(df_reg) > 500:
            st.caption(f"Showing first 500 of {len(df_reg):,} rows — download the workbook for the full data.")
    with tab_hsn:
        if hsn_summary.empty:
            st.info("No HSN Code was found on any item line — Tally's export/live pull didn't carry it, or none is set on these stock items.")
        else:
            st.dataframe(hsn_summary, use_container_width=True, hide_index=True)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_reg.to_excel(writer, sheet_name=f"{register_type} Register", index=False)
        summary_cols = ["Date", "Voucher Type", "Voucher No", "Party Ledger", "Reference", "Voucher Total"]
        summary_cols += [c for c in _GST_COLS if c in unique_vouchers.columns]
        unique_vouchers[summary_cols].to_excel(writer, sheet_name="Voucher Summary", index=False)
        hsn_summary.to_excel(writer, sheet_name="HSN Summary", index=False)
    st.download_button(
        f"⬇ Download {register_type} Register workbook",
        buf.getvalue(),
        file_name=f"tally_{register_type.lower()}_register.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="reg_download",
    )


def _render_register_tab():
    tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

    with tab_upload:
        st.caption(
            "Same Data Interchange export the Extraction activity's Upload tab reads — "
            "this pulls the item-wise inventory detail out of it instead of the ledger entries."
        )
        uploaded = st.file_uploader("Tally export (JSON or XML)", type=["json", "xml"], key="reg_upload_file")
        register_type_u = st.radio("Register", ["Sales", "Purchase"], horizontal=True, key="reg_register_type_upload")
        include_cancelled_u = st.checkbox("Include cancelled/optional vouchers", value=False, key="reg_ic_upload")

        if uploaded and st.button("Extract", type="primary", key="reg_extract_upload"):
            with tempfile.TemporaryDirectory() as tmpdir:
                in_path = os.path.join(tmpdir, uploaded.name or "Transactions.json")
                with open(in_path, "wb") as f:
                    f.write(uploaded.getvalue())
                try:
                    with st.spinner("Checking encoding…"):
                        utf8_path = ensure_utf8(in_path)
                    ledger_master, _rows = extract_any_with_progress(utf8_path)
                    with st.spinner(f"Extracting {register_type_u} register from the export…"):
                        rows = extract_register_from_export(
                            utf8_path, {register_type_u}, include_cancelled=include_cancelled_u,
                            ledger_master=ledger_master,
                        )
                except Exception as e:
                    st.error(f"Extraction failed: {e}")
                    st.stop()
                _render_register_results(pd.DataFrame(rows), register_type_u)

    with tab_live:
        st.caption(
            "Item-wise Sales/Purchase register pulled live from Tally — same connection as above; "
            "both dates required."
        )
        register_type_l = st.radio("Register", ["Sales", "Purchase"], horizontal=True, key="reg_register_type_live")
        c1, c2, c3 = st.columns(3)
        with c1:
            include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="reg_ic_live")
        with c2:
            from_date_l = st.date_input("From date", value=_current_fy_start(), key="reg_fd_live", **_DATE_KW)
        with c3:
            to_date_l = st.date_input("To date", value=datetime.date.today(), key="reg_td_live", **_DATE_KW)

        if st.button(f"Pull {register_type_l} Register", type="primary", key="reg_pull_live"):
            try:
                with st.spinner("Pulling the ledger master (for the GST breakup)…"):
                    ledger_master = tally_connector.fetch_ledger_master(host, port, company)
                with st.spinner(f"Pulling {register_type_l} vouchers from Tally…"):
                    rows = tally_connector.fetch_voucher_register(
                        host, port, company, {register_type_l},
                        from_date_l if isinstance(from_date_l, datetime.date) else None,
                        to_date_l if isinstance(to_date_l, datetime.date) else None,
                        include_cancelled=include_cancelled_l, ledger_master=ledger_master,
                    )
            except tally_connector.TallyConnectionError as e:
                st.error(str(e))
                st.stop()
            except Exception as e:
                st.error(f"Pull failed: {e}")
                st.stop()
            _render_register_results(pd.DataFrame(rows), register_type_l)

    with st.expander("What this does"):
        st.markdown(
            """
- One row per stock item line on each Sales/Purchase voucher: Date, Voucher Type,
  Voucher No, Party Ledger, Reference, Narration, Stock Item, HSN Code, Quantity, Rate,
  Item Amount, plus the voucher's overall value (the GST-inclusive invoice total, from the
  non-party ledger entries) for a cross-check.
- **GST breakup** (CGST/SGST/IGST/UTGST/CESS) is computed per voucher from its own ledger
  entries, using the same heuristic the GST Summary activity uses to recognise a GST ledger.
- **HSN Summary tab** — one row per HSN Code x Voucher Type, matching GSTR-1 Table 12's shape.
- Cancelled/optional vouchers are excluded by default, matching Tally's own normal view.
"""
        )


# ═════════════════════════════════════════════════════════════════════════
# Activity: GST Summary
# ═════════════════════════════════════════════════════════════════════════
def _render_gst_results(df: pd.DataFrame, ledger_master: dict) -> None:
    with st.spinner("Classifying GST ledgers and building the month-wise summary…"):
        detail = build_gst_summary(df, ledger_master)
        pivot = build_month_pivot(detail)
        unclassified = build_unclassified(detail)

    if detail.empty:
        st.warning(
            "No ledgers in this pull were recognisable as GST (CGST/SGST/IGST/UTGST/Cess) "
            "ledgers — check the date range and that the company's Duties & Taxes ledgers "
            "use one of those names."
        )
        return

    st.session_state["gst_last_pivot"] = pivot

    st.divider()
    total_output = pivot["Total Output"].sum() if not pivot.empty else 0.0
    total_input = pivot["Total Input"].sum() if not pivot.empty else 0.0
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Months", len(pivot))
    k2.metric("Total Output (books)", f"{total_output:,.2f}")
    k3.metric("Total Input (books)", f"{total_input:,.2f}")
    k4.metric("Net GST Payable (books)", f"{(total_output - total_input):,.2f}")

    if not unclassified.empty:
        st.warning(
            f"{len(unclassified)} GST-recognisable ledger entries could not be placed as "
            "Output or Input (e.g. a GST adjustment posted via a Journal voucher) — "
            "see the Unclassified tab below before treating the totals above as final."
        )

    tab_pivot, tab_detail, tab_unclassified = st.tabs(
        ["📅 Month-wise Summary", "📋 Ledger Detail", f"⚠️ Unclassified ({len(unclassified)})"]
    )
    with tab_pivot:
        st.dataframe(pivot, use_container_width=True, hide_index=True)
        st.caption(
            "Taxable Value isn't computed here — this is a tax-ledger summary, not an "
            "invoice-level one. See the Sales & Purchase Register activity for per-invoice detail."
        )
    with tab_detail:
        st.dataframe(detail, use_container_width=True, hide_index=True)
    with tab_unclassified:
        if unclassified.empty:
            st.success("Nothing unclassified — every recognised GST ledger entry was placed as Output or Input.")
        else:
            st.dataframe(unclassified, use_container_width=True, hide_index=True)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pivot.to_excel(writer, sheet_name="Month-wise Summary", index=False)
        detail.to_excel(writer, sheet_name="Ledger Detail", index=False)
        unclassified.to_excel(writer, sheet_name="Unclassified", index=False)
    st.download_button(
        "⬇ Download GST Summary workbook", buf.getvalue(),
        file_name="tally_gst_summary.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="gst_download",
    )


def _render_gst_tab():
    tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

    with tab_upload:
        uploaded = st.file_uploader("Tally export (JSON or XML)", type=["json", "xml"], key="gst_upload_file")
        include_cancelled_u = st.checkbox("Include cancelled/optional vouchers", value=False, key="gst_ic_upload")

        if uploaded and st.button("Build GST Summary", type="primary", key="gst_extract_upload"):
            with tempfile.TemporaryDirectory() as tmpdir:
                in_path = os.path.join(tmpdir, uploaded.name or "Transactions.json")
                with open(in_path, "wb") as f:
                    f.write(uploaded.getvalue())
                try:
                    with st.spinner("Checking encoding…"):
                        utf8_path = ensure_utf8(in_path)
                    ledger_master, rows = extract_any_with_progress(utf8_path)
                    with st.spinner("Filtering transactions…"):
                        df, _summary = build_tables(
                            ledger_master, rows, include_cancelled=include_cancelled_u,
                            from_date=None, to_date=None, ledger_filter=None,
                        )
                except SystemExit as e:
                    st.error(str(e))
                    st.stop()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")
                    st.stop()
                _render_gst_results(df, ledger_master)

    with tab_live:
        include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="gst_ic_live")
        c1, c2 = st.columns(2)
        with c1:
            from_date_l = st.date_input("From date", value=_current_fy_start(), key="gst_fd_live", **_DATE_KW)
        with c2:
            to_date_l = st.date_input("To date", value=datetime.date.today(), key="gst_td_live", **_DATE_KW)

        if st.button("Pull from Tally", type="primary", key="gst_pull_live"):
            try:
                with st.spinner("Pulling ledgers and vouchers from Tally…"):
                    ledger_master, rows = tally_connector.pull_from_tally(host, port, company, from_date_l, to_date_l)
                with st.spinner("Filtering transactions…"):
                    df, _summary = build_tables(
                        ledger_master, rows, include_cancelled=include_cancelled_l,
                        from_date=None, to_date=None, ledger_filter=None,
                    )
            except tally_connector.TallyConnectionError as e:
                st.error(str(e))
                st.stop()
            except SystemExit as e:
                st.error(str(e))
                st.stop()
            except Exception as e:
                st.error(f"Pull failed: {e}")
                st.stop()
            _render_gst_results(df, ledger_master)

    st.divider()
    st.subheader("Reconcile with filed returns")
    st.caption(
        "Compares the books summary above (built via either tab) against filed GSTR-1 and "
        "GSTR-3B PDFs you upload here."
    )

    pivot_for_recon = st.session_state.get("gst_last_pivot")
    if pivot_for_recon is None or pivot_for_recon.empty:
        st.info("Build a GST Summary above first (Upload or Live tab) — this section reconciles that result.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            gstr1_files = st.file_uploader(
                "GSTR-1 PDF(s)", type=["pdf"], accept_multiple_files=True, key="gst_recon_gstr1_files"
            )
        with c2:
            gstr3b_files = st.file_uploader(
                "GSTR-3B PDF(s)", type=["pdf"], accept_multiple_files=True, key="gst_recon_gstr3b_files"
            )
        gstin_input = st.text_input(
            "GSTIN (optional — auto-detected from the uploaded returns if left blank)",
            key="gst_recon_gstin",
        )

        if st.button("Reconcile", type="primary", key="gst_recon_button"):
            if not gstr1_files and not gstr3b_files:
                st.warning("Upload at least one GSTR-1 or GSTR-3B PDF to reconcile against.")
                st.stop()

            gstr1_rows, gstr3b_rows, warnings = [], [], []
            with st.spinner("Reading filed returns…"):
                for f in gstr1_files or []:
                    text = read_pdf_text(f.getvalue())
                    if detect_statutory_type(text) != "GSTR1":
                        warnings.append(f"{f.name}: doesn't look like a GSTR-1 — skipped.")
                        continue
                    gstr1_rows.append(extract_gstr1(f.name, text))
                for f in gstr3b_files or []:
                    text = read_pdf_text(f.getvalue())
                    if detect_statutory_type(text) != "GSTR3B":
                        warnings.append(f"{f.name}: doesn't look like a GSTR-3B — skipped.")
                        continue
                    gstr3b_rows.append(extract_gstr3b(f.name, text))

            for w in warnings:
                st.warning(w)

            resolved_gstin, note = resolve_gstin(gstin_input, gstr1_rows, gstr3b_rows)
            if note:
                (st.info if resolved_gstin else st.error)(note)
            if not resolved_gstin:
                st.stop()

            result, collisions = compute_recon_3way(pivot_for_recon, resolved_gstin, gstr1_rows, gstr3b_rows)
            if collisions:
                st.warning(
                    f"The books pivot has more than one calendar year's data for: "
                    f"{', '.join(sorted(collisions))} — those months were summed across years "
                    "rather than kept apart. For a clean match, pull one financial year at a time."
                )

            matched = int((result["Status"] == "Matched").sum())
            mismatched = int((result["Status"] == "Books vs Return Mismatch").sum())
            k1, k2, k3 = st.columns(3)
            k1.metric("Months compared", len(result))
            k2.metric("Matched", matched)
            k3.metric("Mismatched / one-sided", len(result) - matched)
            if mismatched:
                st.warning(f"{mismatched} month(s) show a Books-vs-Return mismatch beyond ₹1 tolerance — see below.")

            st.dataframe(result, use_container_width=True, hide_index=True)

            buf_recon = io.BytesIO()
            with pd.ExcelWriter(buf_recon, engine="openpyxl") as writer:
                result.to_excel(writer, sheet_name="3-Way GST Recon", index=False)
            st.download_button(
                "⬇ Download reconciliation workbook", buf_recon.getvalue(),
                file_name="tally_gst_recon.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="gst_recon_download",
            )

    with st.expander("How ledgers are classified"):
        st.markdown(
            """
- A ledger is treated as GST-related if its name contains **CGST / SGST / IGST / UTGST /
  Cess** (case-insensitive, whole word).
- **Output vs Input** is decided first by keywords in the ledger name itself, then, if the
  name gives no direction, by the **Voucher Type** of each entry's voucher.
- Anything recognisable as a GST ledger but that still can't be placed is reported in the
  **Unclassified** tab rather than silently netted into the payable figure.
- This is a heuristic, not a read of Tally's own GST metadata, which is inconsistently
  populated across versions/releases.
- **Reconciliation join key**: filed GSTR-1/GSTR-3B PDFs carry a bare month name, not a
  year — pulling more than one financial year into the same reconciliation will sum
  same-named months across years together (flagged when it happens).
"""
        )


# ═════════════════════════════════════════════════════════════════════════
# Activity: TDS Summary
# ═════════════════════════════════════════════════════════════════════════
def _render_tds_results(df: pd.DataFrame, ledger_master: dict) -> None:
    with st.spinner("Classifying TDS ledgers and tracing parties…"):
        detail = build_tds_summary(df, ledger_master)

    if detail.empty:
        st.warning(
            "No ledgers in this pull were recognisable as TDS ledgers — check the date "
            "range and that the company's TDS ledgers have \"TDS\" in the name."
        )
        return

    st.session_state["tds_last_detail"] = detail

    inferred_count = int(detail["Party Inferred"].sum())
    st.divider()
    k1, k2, k3 = st.columns(3)
    k1.metric("Total TDS Deducted (books)", f"{detail['TDS Amount'].sum():,.2f}")
    k2.metric("Parties", detail["Party (Deductee)"].nunique())
    k3.metric("Rows with inferred party", inferred_count)

    if inferred_count:
        st.warning(
            f"{inferred_count} row(s) had no Party Ledger set on the voucher — the party "
            "shown was inferred from the largest non-TDS, non-cash/bank ledger entry on the "
            "same voucher. Verify these (\"Party Inferred\" = True) before relying on them."
        )

    st.dataframe(detail, use_container_width=True, hide_index=True)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        detail.to_excel(writer, sheet_name="TDS Summary", index=False)
    st.download_button(
        "⬇ Download TDS Summary workbook", buf.getvalue(),
        file_name="tally_tds_summary.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="tds_download",
    )


def _render_tds_tab():
    tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

    with tab_upload:
        uploaded = st.file_uploader("Tally export (JSON or XML)", type=["json", "xml"], key="tds_upload_file")
        include_cancelled_u = st.checkbox("Include cancelled/optional vouchers", value=False, key="tds_ic_upload")

        if uploaded and st.button("Build TDS Summary", type="primary", key="tds_extract_upload"):
            with tempfile.TemporaryDirectory() as tmpdir:
                in_path = os.path.join(tmpdir, uploaded.name or "Transactions.json")
                with open(in_path, "wb") as f:
                    f.write(uploaded.getvalue())
                try:
                    with st.spinner("Checking encoding…"):
                        utf8_path = ensure_utf8(in_path)
                    ledger_master, rows = extract_any_with_progress(utf8_path)
                    with st.spinner("Filtering transactions…"):
                        df, _summary = build_tables(
                            ledger_master, rows, include_cancelled=include_cancelled_u,
                            from_date=None, to_date=None, ledger_filter=None,
                        )
                except SystemExit as e:
                    st.error(str(e))
                    st.stop()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")
                    st.stop()
                _render_tds_results(df, ledger_master)

    with tab_live:
        include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="tds_ic_live")
        c1, c2 = st.columns(2)
        with c1:
            from_date_l = st.date_input("From date", value=_current_fy_start(), key="tds_fd_live", **_DATE_KW)
        with c2:
            to_date_l = st.date_input("To date", value=datetime.date.today(), key="tds_td_live", **_DATE_KW)

        if st.button("Pull from Tally", type="primary", key="tds_pull_live"):
            try:
                with st.spinner("Pulling ledgers and vouchers from Tally…"):
                    ledger_master, rows = tally_connector.pull_from_tally(host, port, company, from_date_l, to_date_l)
                with st.spinner("Filtering transactions…"):
                    df, _summary = build_tables(
                        ledger_master, rows, include_cancelled=include_cancelled_l,
                        from_date=None, to_date=None, ledger_filter=None,
                    )
            except tally_connector.TallyConnectionError as e:
                st.error(str(e))
                st.stop()
            except SystemExit as e:
                st.error(str(e))
                st.stop()
            except Exception as e:
                st.error(f"Pull failed: {e}")
                st.stop()
            _render_tds_results(df, ledger_master)

    st.divider()
    st.subheader("Reconcile with deposited challans")
    st.caption(
        "Compares the books total above against ITNS-281 TDS challan PDFs you upload here, "
        "at the Financial Year level."
    )

    detail_for_recon = st.session_state.get("tds_last_detail")
    if detail_for_recon is None or detail_for_recon.empty:
        st.info("Build a TDS Summary above first (Upload or Live tab) — this section reconciles that result.")
    else:
        challan_files = st.file_uploader(
            "TDS challan PDF(s) (ITNS-281)", type=["pdf"], accept_multiple_files=True, key="tds_recon_challan_files"
        )

        if st.button("Reconcile", type="primary", key="tds_recon_button"):
            if not challan_files:
                st.warning("Upload at least one TDS challan PDF to reconcile against.")
                st.stop()

            challan_rows, warnings = [], []
            with st.spinner("Reading challans…"):
                for f in challan_files:
                    text = read_pdf_text(f.getvalue())
                    if detect_statutory_type(text) != "TDS":
                        warnings.append(f"{f.name}: doesn't look like a TDS challan (ITNS-281) — skipped.")
                        continue
                    challan_rows.append(extract_tds(f.name, text))

            for w in warnings:
                st.warning(w)
            if not challan_rows:
                st.error("None of the uploaded files were recognised as TDS challans.")
                st.stop()

            result = build_tds_recon(detail_for_recon, challan_rows)

            matched = int((result["Status"] == "Matched").sum())
            under = int((result["Status"] == "Under-deposited").sum())
            over = int((result["Status"] == "Over-deposited").sum())
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Years compared", len(result))
            k2.metric("Matched", matched)
            k3.metric("Under-deposited", under)
            k4.metric("Over-deposited", over)
            if under:
                st.warning(f"{under} financial year(s) show TDS deducted exceeding what was deposited — check for a shortfall.")

            st.dataframe(result, use_container_width=True, hide_index=True)

            buf_recon = io.BytesIO()
            with pd.ExcelWriter(buf_recon, engine="openpyxl") as writer:
                result.to_excel(writer, sheet_name="TDS Recon", index=False)
            st.download_button(
                "⬇ Download reconciliation workbook", buf_recon.getvalue(),
                file_name="tally_tds_recon.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="tds_recon_download",
            )

    with st.expander("How this works"):
        st.markdown(
            """
- A ledger is treated as a TDS ledger if its name contains **"TDS"** (case-insensitive, whole word).
- **Nature of Payment / Section** is a best-effort guess from keywords in the ledger name —
  comes back **"Unclassified"** rather than a wrong guess when nothing matches.
- **Party (Deductee)** is read from the voucher's own Party Ledger field, or inferred (flagged)
  when that's blank.
- **Challan reconciliation** is Financial-Year-total only — a challan doesn't carry a month or section.
"""
        )


# ═════════════════════════════════════════════════════════════════════════
# Activity: Bank Reconciliation
# ═════════════════════════════════════════════════════════════════════════
def _pick_bank_ledger(ledger_master: dict, key_prefix: str) -> str | None:
    bank_ledgers = sorted(
        n for n, v in ledger_master.items() if "bank" in (v.get("group") or "").lower()
    )
    options = bank_ledgers or sorted(ledger_master.keys())
    if not options:
        st.warning("No ledgers found in this pull.")
        return None
    if not bank_ledgers:
        st.caption("No ledger's Group name contains \"Bank\" — showing every ledger instead.")
    return st.selectbox("Bank Ledger", options, key=f"{key_prefix}_ledger_select")


def _render_bank_recon(df, ledger_master: dict, key_prefix: str) -> None:
    bank_ledger = _pick_bank_ledger(ledger_master, key_prefix)
    if not bank_ledger:
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        as_of_date = st.date_input("As on date", value=datetime.date.today(), key=f"{key_prefix}_as_of", **_DATE_KW)
    with c2:
        window_days = st.number_input("Match window (days back)", min_value=1, max_value=365, value=60, key=f"{key_prefix}_window")
    with c3:
        date_tolerance = st.number_input("Date tolerance for matching (days)", min_value=0, max_value=60, value=7, key=f"{key_prefix}_tolerance")

    book_balance, last_txn_date = book_balance_as_of(df, bank_ledger, as_of_date)
    st.metric(f"Balance as per Books — {bank_ledger} (as on {as_of_date})", f"{book_balance:,.2f}")
    if last_txn_date is None:
        st.caption("No transactions on/before this date — showing the ledger's Opening Balance.")
    elif last_txn_date != as_of_date:
        st.caption(f"No transaction exactly on {as_of_date} — using the balance as of the last transaction, {last_txn_date}.")

    st.divider()
    st.subheader("Upload bank statement")
    uploaded = st.file_uploader("Bank statement (CSV or Excel)", type=["csv", "xlsx", "xls"], key=f"{key_prefix}_stmt_file")
    if not uploaded:
        st.info("Upload a statement to run the reconciliation.")
        return

    try:
        stmt_raw = load_single_file(uploaded)
    except Exception as e:
        st.error(f"Couldn't read that file: {e}")
        return
    if stmt_raw.empty:
        st.warning("That file has no rows.")
        return

    cols = list(stmt_raw.columns)
    st.caption("Map the statement's columns:")
    c1, c2 = st.columns(2)
    with c1:
        date_col = st.selectbox("Date column", cols, key=f"{key_prefix}_date_col")
    with c2:
        reference_col = st.selectbox("Reference/Narration column (optional)", ["(none)"] + cols, key=f"{key_prefix}_ref_col")
        reference_col = None if reference_col == "(none)" else reference_col

    amount_mode = st.radio(
        "Amount columns", ["Separate Deposit/Withdrawal columns", "One signed Amount column"],
        horizontal=True, key=f"{key_prefix}_amount_mode",
    )
    deposit_col = withdrawal_col = amount_col = None
    if amount_mode == "One signed Amount column":
        amount_col = st.selectbox(
            "Amount column (positive = deposit/receipt, negative = withdrawal/payment)", cols, key=f"{key_prefix}_amount_col"
        )
    else:
        c1, c2 = st.columns(2)
        with c1:
            deposit_col = st.selectbox("Deposit/Credit column", cols, key=f"{key_prefix}_deposit_col")
        with c2:
            withdrawal_col = st.selectbox("Withdrawal/Debit column", cols, key=f"{key_prefix}_withdrawal_col")

    statement_closing_balance = st.number_input(
        f"Bank statement's own closing balance as on {as_of_date} (optional, from the statement itself)",
        value=0.0, step=0.01, key=f"{key_prefix}_stmt_balance",
        help="Leave at 0 to skip the top-line tie-out and just see the reconciling items below.",
    )
    has_closing_balance = st.checkbox("I entered the statement's actual closing balance above", key=f"{key_prefix}_has_balance")

    if st.button("Reconcile", type="primary", key=f"{key_prefix}_reconcile_button"):
        try:
            stmt_df = normalize_statement(
                stmt_raw, date_col=date_col, deposit_col=deposit_col, withdrawal_col=withdrawal_col,
                amount_col=amount_col, reference_col=reference_col,
            )
        except Exception as e:
            st.error(f"Couldn't map those columns: {e}")
            return

        window_start = as_of_date - datetime.timedelta(days=int(window_days))
        book_window = df[
            (df["Ledger Name"] == bank_ledger)
            & df["Date"].apply(lambda d: d is not None and window_start <= d <= as_of_date)
        ].copy()
        book_window["Amount"] = book_window["Debit"] - book_window["Credit"]
        book_window["Reference"] = book_window.get("Narration", "")
        stmt_window = stmt_df[stmt_df["Date"].apply(lambda d: d is not None and window_start <= d <= as_of_date)]

        matched, book_only, stmt_only = match_statement_to_books(book_window, stmt_window, int(date_tolerance))
        brs = build_brs(
            book_only, stmt_only, book_balance,
            statement_closing_balance if has_closing_balance else None,
        )

        st.divider()
        k1, k2, k3 = st.columns(3)
        k1.metric("Matched", len(matched))
        k2.metric("Book-only (reconciling)", len(book_only))
        k3.metric("Statement-only (follow up)", len(stmt_only))

        st.subheader("Bank Reconciliation Statement")
        brs_rows = [
            ("Balance as per Bank Statement" + (f" (as on {as_of_date})" if has_closing_balance else " — not entered"),
             brs["statement_closing_balance"]),
            ("Add: Deposits/receipts in books, not yet credited by the bank", brs["deposits_not_credited"]),
            ("Less: Cheques/payments issued, not yet presented for payment", -brs["cheques_not_presented"]),
            ("Add/Less: Items in statement, not yet recorded in books (net)", -brs["items_in_statement_not_in_books"]),
            ("= Balance as per Books (computed)", brs["computed_book_balance"]),
            ("Balance as per Books (actual, from Tally)", brs["book_balance"]),
        ]
        brs_df = pd.DataFrame(brs_rows, columns=["Particulars", "Amount"])
        st.dataframe(brs_df, use_container_width=True, hide_index=True)

        if brs["ties"] is True:
            st.success(f"Ties out — computed and actual book balances match (difference {brs['tie_difference']:,.2f}).")
        elif brs["ties"] is False:
            st.warning(
                f"Does not tie — difference of {brs['tie_difference']:,.2f}. Check the Book-only and "
                "Statement-only tabs below for what might explain it."
            )
        else:
            st.info("Enter and confirm the statement's actual closing balance above for a should-tie check.")

        tab_matched, tab_book_only, tab_stmt_only = st.tabs(
            [f"✅ Matched ({len(matched)})", f"📖 Book-only ({len(book_only)})", f"🏦 Statement-only ({len(stmt_only)})"]
        )
        with tab_matched:
            st.dataframe(matched, use_container_width=True, hide_index=True)
        with tab_book_only:
            if book_only.empty:
                st.success("No unmatched book entries in this window.")
            else:
                st.dataframe(book_only, use_container_width=True, hide_index=True)
        with tab_stmt_only:
            if stmt_only.empty:
                st.success("No unmatched statement entries in this window.")
            else:
                st.dataframe(stmt_only, use_container_width=True, hide_index=True)
                st.caption("Bank charges, interest, or a direct credit/debit never booked — verify and post as needed.")

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            brs_df.to_excel(writer, sheet_name="BRS", index=False)
            matched.to_excel(writer, sheet_name="Matched", index=False)
            book_only.to_excel(writer, sheet_name="Book-only", index=False)
            stmt_only.to_excel(writer, sheet_name="Statement-only", index=False)
        st.download_button(
            "⬇ Download Bank Reconciliation workbook", buf.getvalue(),
            file_name="tally_bank_reconciliation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key_prefix}_download",
        )


def _render_bank_recon_tab():
    tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

    with tab_upload:
        uploaded_export = st.file_uploader("Tally export (JSON or XML)", type=["json", "xml"], key="brs_upload_file")
        include_cancelled_u = st.checkbox("Include cancelled/optional vouchers", value=False, key="brs_ic_upload")

        if uploaded_export and st.button("Load Ledgers", type="primary", key="brs_load_upload"):
            with tempfile.TemporaryDirectory() as tmpdir:
                in_path = os.path.join(tmpdir, uploaded_export.name or "Transactions.json")
                with open(in_path, "wb") as f:
                    f.write(uploaded_export.getvalue())
                try:
                    with st.spinner("Checking encoding…"):
                        utf8_path = ensure_utf8(in_path)
                    ledger_master, rows = extract_any_with_progress(utf8_path)
                    with st.spinner("Building ledger tables…"):
                        df, _summary = build_tables(
                            ledger_master, rows, include_cancelled=include_cancelled_u,
                            from_date=None, to_date=None, ledger_filter=None,
                        )
                except SystemExit as e:
                    st.error(str(e))
                    st.stop()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")
                    st.stop()
                st.session_state["brs_df"] = df
                st.session_state["brs_ledger_master"] = ledger_master

        if "brs_df" in st.session_state:
            _render_bank_recon(st.session_state["brs_df"], st.session_state["brs_ledger_master"], key_prefix="brs_u")

    with tab_live:
        include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="brs_ic_live")
        c1, c2 = st.columns(2)
        with c1:
            from_date_l = st.date_input("From date", value=_current_fy_start(), key="brs_fd_live", **_DATE_KW)
        with c2:
            to_date_l = st.date_input("To date", value=datetime.date.today(), key="brs_td_live", **_DATE_KW)

        if st.button("Pull from Tally", type="primary", key="brs_pull_live"):
            try:
                with st.spinner("Pulling ledgers and vouchers from Tally…"):
                    ledger_master, rows = tally_connector.pull_from_tally(host, port, company, from_date_l, to_date_l)
                with st.spinner("Building ledger tables…"):
                    df, _summary = build_tables(
                        ledger_master, rows, include_cancelled=include_cancelled_l,
                        from_date=None, to_date=None, ledger_filter=None,
                    )
            except tally_connector.TallyConnectionError as e:
                st.error(str(e))
                st.stop()
            except SystemExit as e:
                st.error(str(e))
                st.stop()
            except Exception as e:
                st.error(f"Pull failed: {e}")
                st.stop()
            st.session_state["brs_df_live"] = df
            st.session_state["brs_ledger_master_live"] = ledger_master

        if "brs_df_live" in st.session_state:
            _render_bank_recon(st.session_state["brs_df_live"], st.session_state["brs_ledger_master_live"], key_prefix="brs_l")

    with st.expander("How this works"):
        st.markdown(
            """
- **Balance as per Books** is read off the Extraction activity's own Running Balance column,
  sliced to the last transaction on or before the date you pick.
- **Sign convention**: on both sides, positive = increases the bank balance (a deposit/receipt).
- **Matching**: same amount (within a paisa) and within your chosen date-tolerance window.
- **Statement closing balance** is a number you enter yourself, not parsed from the upload.
"""
        )


# ═════════════════════════════════════════════════════════════════════════
# Activity: Inventory Closing Stock
# ═════════════════════════════════════════════════════════════════════════
def _render_stock_results(summary: pd.DataFrame) -> None:
    if summary.empty:
        st.warning("No stock items found in this pull.")
        return

    negative = build_negative_stock_flags(summary)
    material_variance = summary[summary["Variance Qty"].abs() > 0.01]

    st.divider()
    k1, k2, k3 = st.columns(3)
    k1.metric("Stock items", len(summary))
    k2.metric("Items with variance", len(material_variance))
    k3.metric("Negative-stock items", len(negative))

    if not negative.empty:
        st.error(
            f"{len(negative)} item(s) show a negative closing quantity — a classic red flag. "
            "See the Negative Stock tab."
        )

    tab_summary, tab_variance, tab_negative = st.tabs(
        ["📋 Full Summary", f"⚠️ Variance ({len(material_variance)})", f"🚩 Negative Stock ({len(negative)})"]
    )
    with tab_summary:
        st.dataframe(summary, use_container_width=True, hide_index=True)
    with tab_variance:
        if material_variance.empty:
            st.success("No variance beyond rounding — Tally's reported closing matches the derived figure for every item.")
        else:
            st.dataframe(material_variance, use_container_width=True, hide_index=True)
            st.caption(
                "Variance here is expected wherever the company uses Stock Journal/Manufacturing "
                "Journal vouchers (not captured by this pass) or has material Sales/Purchase Returns."
            )
    with tab_negative:
        if negative.empty:
            st.success("No item shows a negative closing quantity.")
        else:
            st.dataframe(negative, use_container_width=True, hide_index=True)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Stock Summary", index=False)
        material_variance.to_excel(writer, sheet_name="Variance", index=False)
        negative.to_excel(writer, sheet_name="Negative Stock", index=False)
    st.download_button(
        "⬇ Download Inventory Closing Stock workbook", buf.getvalue(),
        file_name="tally_inventory_closing_stock.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="inv_download",
    )


def _render_inventory_tab():
    tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

    with tab_upload:
        st.caption(
            "Same Data Interchange export the other activities read — stock item masters and "
            "the Sales/Purchase item movements both come from this one file."
        )
        uploaded = st.file_uploader("Tally export (JSON or XML)", type=["json", "xml"], key="inv_upload_file")
        include_cancelled_u = st.checkbox("Include cancelled/optional vouchers", value=False, key="inv_ic_upload")

        if uploaded and st.button("Build Inventory Summary", type="primary", key="inv_extract_upload"):
            with tempfile.TemporaryDirectory() as tmpdir:
                in_path = os.path.join(tmpdir, uploaded.name or "Transactions.json")
                with open(in_path, "wb") as f:
                    f.write(uploaded.getvalue())
                try:
                    with st.spinner("Checking encoding…"):
                        utf8_path = ensure_utf8(in_path)
                    with st.spinner("Looking for Stock Item masters in the export…"):
                        stock_items = extract_stock_items_from_export(utf8_path)
                    if not stock_items:
                        st.warning(
                            "No Stock Item masters found in this export — Tally's JSON/XML Data "
                            "Interchange export may not include them for this company/export "
                            "setting. Try **Connect to Tally (live)** instead."
                        )
                        st.stop()
                    with st.spinner("Extracting Sales & Purchase item movements…"):
                        sales_rows = extract_register_from_export(utf8_path, {"Sales"}, include_cancelled=include_cancelled_u)
                        purchase_rows = extract_register_from_export(utf8_path, {"Purchase"}, include_cancelled=include_cancelled_u)
                except Exception as e:
                    st.error(f"Extraction failed: {e}")
                    st.stop()
                summary = build_stock_summary(stock_items, sales_rows + purchase_rows)
                _render_stock_results(summary)

    with tab_live:
        st.caption(
            "Pulls the Stock Item master (opening figures + Tally's own reported closing) plus the "
            "Sales and Purchase registers for the period."
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="inv_ic_live")
        with c2:
            from_date_l = st.date_input(
                "From date (start of the movement period)", value=_current_fy_start(), key="inv_fd_live", **_DATE_KW
            )
        with c3:
            to_date_l = st.date_input("As on date", value=datetime.date.today(), key="inv_td_live", **_DATE_KW)

        if st.button("Pull Inventory Closing Stock", type="primary", key="inv_pull_live"):
            try:
                with st.spinner("Pulling stock item masters from Tally…"):
                    stock_items = tally_connector.fetch_stock_items(host, port, company)
                with st.spinner("Pulling Sales register movements…"):
                    sales_rows = tally_connector.fetch_voucher_register(
                        host, port, company, {"Sales"}, from_date_l, to_date_l, include_cancelled=include_cancelled_l,
                    )
                with st.spinner("Pulling Purchase register movements…"):
                    purchase_rows = tally_connector.fetch_voucher_register(
                        host, port, company, {"Purchase"}, from_date_l, to_date_l, include_cancelled=include_cancelled_l,
                    )
            except tally_connector.TallyConnectionError as e:
                st.error(str(e))
                st.stop()
            except Exception as e:
                st.error(f"Pull failed: {e}")
                st.stop()
            summary = build_stock_summary(stock_items, sales_rows + purchase_rows)
            _render_stock_results(summary)

    with st.expander("What this does"):
        st.markdown(
            """
- **Opening Qty/Value** and **Tally Reported Closing Qty/Value** come straight from Tally's
  StockItem master.
- **Derived Closing Qty** = Opening + Purchases Qty − Sales Qty, using the Sales/Purchase
  Register's item-wise quantities.
- **Variance Qty** = Tally Reported − Derived — the audit-relevant signal, not a bug to chase
  to zero.
- **Negative Stock tab** — any item with a negative Derived or Tally-reported closing quantity.
"""
        )


# ═════════════════════════════════════════════════════════════════════════
tab_extract, tab_register, tab_gst, tab_tds, tab_bank, tab_inventory = st.tabs([
    "📒 Extraction", "🧾 Sales & Purchase Register", "🧮 GST Summary",
    "🧾 TDS Summary", "🏦 Bank Reconciliation", "📦 Inventory Closing Stock",
])

with tab_extract:
    _render_extraction_tab()
with tab_register:
    _render_register_tab()
with tab_gst:
    _render_gst_tab()
with tab_tds:
    _render_tds_tab()
with tab_bank:
    _render_bank_recon_tab()
with tab_inventory:
    _render_inventory_tab()

footer()
