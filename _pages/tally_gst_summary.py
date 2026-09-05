"""Hub page: Tally GST Summary (Output/Input), as per books.

Month-wise Output/Input tax summary built purely from Tally's own ledger
entries (tally_tool/reports/gst_summary.py) -- classification is heuristic
(ledger name + parent group + voucher type), not read off Tally's own GST
metadata, which is inconsistently populated across versions. See that
module's docstring for the classification rules.

A second section below lets you reconcile the books summary against filed
GSTR-1/GSTR-3B returns (Phase 2) -- upload the same PDFs Combined_PF_Statutory.py's
PF & Statutory page already knows how to read, parsed here via the shared
common/statutory_extractors.py module so both pages read filed returns
identically.

Reuses extract_ledgers.build_tables() for the cancelled/optional filtering
and ledger-group lookup it already does for the ledger extraction page, so
this page's classification logic only has to deal with clean, already-
filtered transaction rows -- same shared-plumbing principle as the rest of
tally_tool.
"""
import datetime
import io
import os
import sys
import tempfile

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tally_tool"))

from _pages.theme import page_header, footer
from _pages.tally_common import render_connection_picker, render_setup_help

from common.statutory_extractors import detect_statutory_type, extract_gstr1, extract_gstr3b, read_pdf_text

from extract_ledgers import ensure_utf8, extract_any, build_tables
from reports.gst_summary import build_gst_summary, build_month_pivot, build_unclassified
from reports.gst_recon import compute_recon_3way, resolve_gstin
import tally_connector

page_header(
    "🧮", "Tally: GST Summary",
    "Month-wise Output/Input GST summary, as per Tally's own books — from an uploaded "
    "export or a live Tally connection.",
    badges=["As per books", "Month-wise Output vs Input", "3-way recon vs filed returns"],
)


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

    # Stashed so the "Reconcile with filed returns" section below (which
    # reruns independently, on its own button) can use the last-built
    # pivot without the user having to re-pull from Tally.
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
            "invoice-level one. See the Sales & Purchase Register page for per-invoice detail."
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
        "⬇ Download GST Summary workbook",
        buf.getvalue(),
        file_name="tally_gst_summary.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

# ── Tab 1: upload a JSON or XML export ──────────────────────────────────────
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
                with st.spinner("Streaming the export…"):
                    ledger_master, rows = extract_any(utf8_path)
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

# ── Tab 2: connect to a running Tally instance ──────────────────────────────
with tab_live:
    render_setup_help(expanded=False)
    host, port, company = render_connection_picker("tally_gst")

    include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="gst_ic_live")
    c1, c2 = st.columns(2)
    with c1:
        from_date_l = st.date_input(
            "From date", value=datetime.date(2000, 1, 1), format="YYYY-MM-DD", key="gst_fd_live",
            min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1),
        )
    with c2:
        to_date_l = st.date_input(
            "To date", value=datetime.date.today(), format="YYYY-MM-DD", key="gst_td_live",
            min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1),
        )

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
    "GSTR-3B PDFs you upload here — the same reconciliation Combined_PF_Statutory.py's "
    "PF & Statutory page runs between GSTR-1 and GSTR-3B, extended with a third leg."
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
                "rather than kept apart (see this page's 'How ledgers are classified' note). "
                "For a clean match, pull one financial year at a time."
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
            "⬇ Download reconciliation workbook",
            buf_recon.getvalue(),
            file_name="tally_gst_recon.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="gst_recon_download",
        )

with st.expander("How ledgers are classified"):
    st.markdown(
        """
- A ledger is treated as GST-related if its name contains **CGST / SGST / IGST / UTGST /
  Cess** (case-insensitive, whole word).
- **Output vs Input** is decided first by keywords in the ledger name itself
  (Output/Payable/Liability/Collected → Output; Input/ITC/Credit/Receivable → Input).
- If the name gives no direction (a common real-world pattern, e.g. a bare **"CGST"**
  ledger used for both legs), each *entry* is instead resolved by the **Voucher Type**
  of its voucher — Sales/Sales Return/Credit Note → Output; Purchase/Purchase Return/
  Debit Note → Input.
- Anything recognisable as a GST ledger but that still can't be placed (e.g. a GST
  adjustment posted via a Journal voucher) is reported in the **Unclassified** tab
  rather than silently netted into the payable figure.
- **Tax Amount** convention: Output = net Credit − Debit for the period (tax charged,
  net of utilisation); Input = net Debit − Credit (ITC availed, net of utilisation/reversal).
- This is a heuristic, not a read of Tally's own GST metadata (GSTDETAILS.LIST), which is
  inconsistently populated across versions/releases — review the Ledger Detail tab if a
  figure looks off.
- **Reconciliation join key**: filed GSTR-1/GSTR-3B PDFs carry a bare month name ("January"),
  not a year, so the "Reconcile with filed returns" section above matches by month name alone —
  pulling more than one financial year of Tally data into the same reconciliation will sum
  same-named months across years together (flagged when it happens). Run one FY at a time
  for a clean match.
"""
    )

footer()
