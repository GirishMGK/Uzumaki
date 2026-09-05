"""Hub page: Tally TDS Summary (party-wise, deducted), as per books.

TDS deducted from vendors (the payable side -- what the company deducted
before paying a vendor, not what customers deducted from the company, which
form26as_tool already covers). tally_tool/reports/tds_summary.py groups by
Party (Deductee) x Nature of Payment x Month; see that module's docstring
for the classification and party-tracing rules.

A second section below reconciles the books total against deposited TDS
challans (Phase 2) -- upload the same ITNS-281 challan PDFs
Combined_PF_Statutory.py's PF & Statutory page already parses via
extract_tds(), read here through the shared common/statutory_extractors.py
module. A challan only carries a Financial Year and an aggregate deposit
amount (no month or section), so this comparison is at the FY-total level,
not party-wise or month-wise -- see tally_tool/reports/tds_recon.py's
docstring for why.
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

from common.statutory_extractors import detect_statutory_type, extract_tds, read_pdf_text

from extract_ledgers import ensure_utf8, extract_any, build_tables
from reports.tds_summary import build_tds_summary
from reports.tds_recon import build_tds_recon
import tally_connector

page_header(
    "🧾", "Tally: TDS Summary",
    "Party-wise TDS deducted, as per Tally's own books — from an uploaded export or a "
    "live Tally connection.",
    badges=["As per books", "Party-wise & month-wise", "Recon vs deposited challans"],
)


def _render_tds_results(df: pd.DataFrame, ledger_master: dict) -> None:
    with st.spinner("Classifying TDS ledgers and tracing parties…"):
        detail = build_tds_summary(df, ledger_master)

    if detail.empty:
        st.warning(
            "No ledgers in this pull were recognisable as TDS ledgers — check the date "
            "range and that the company's TDS ledgers have \"TDS\" in the name."
        )
        return

    # Stashed so the "Reconcile with deposited challans" section below (its
    # own button, its own rerun) can use the last-built summary without the
    # user having to re-pull from Tally.
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
        "⬇ Download TDS Summary workbook",
        buf.getvalue(),
        file_name="tally_tds_summary.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

# ── Tab 1: upload a JSON or XML export ──────────────────────────────────────
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

            _render_tds_results(df, ledger_master)

# ── Tab 2: connect to a running Tally instance ──────────────────────────────
with tab_live:
    render_setup_help(expanded=False)
    host, port, company = render_connection_picker("tally_tds")

    include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="tds_ic_live")
    c1, c2 = st.columns(2)
    with c1:
        from_date_l = st.date_input(
            "From date", value=datetime.date(2000, 1, 1), format="YYYY-MM-DD", key="tds_fd_live",
            min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1),
        )
    with c2:
        to_date_l = st.date_input(
            "To date", value=datetime.date.today(), format="YYYY-MM-DD", key="tds_td_live",
            min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1),
        )

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
    "Compares the books total above (built via either tab) against ITNS-281 TDS challan "
    "PDFs you upload here, at the Financial Year level — a challan doesn't carry a month "
    "or section, so this can't be finer-grained than an FY total."
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
            "⬇ Download reconciliation workbook",
            buf_recon.getvalue(),
            file_name="tally_tds_recon.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="tds_recon_download",
        )

with st.expander("How this works"):
    st.markdown(
        """
- A ledger is treated as a TDS ledger if its name contains **"TDS"** (case-insensitive,
  whole word) — e.g. "TDS Payable", "TDS on Contractors".
- **Nature of Payment / Section** is a best-effort guess from keywords in the ledger name
  (Contractors→194C, Rent→194I, Professional/Technical→194J, Commission→194H,
  Interest→194A, Salary→192, and a few more) — comes back **"Unclassified"** rather than a
  wrong guess when nothing matches.
- **Party (Deductee)** is read from the voucher's own Party Ledger field. Where that's
  blank (some Payment/Journal vouchers don't set it), the party is **inferred** as the
  largest non-TDS, non-cash/bank ledger entry on the same voucher — these rows are flagged
  so you can verify them.
- **TDS Amount** = net Credit − Debit for the period (TDS is a liability ledger; a Debit
  is typically the eventual payment to the government, or a correction).
- **PAN** isn't available from the current Tally pull and is left blank rather than guessed
  — a future pass can add it once the ledger master's Income Tax Number field is fetched.
- **Challan reconciliation** is Financial-Year-total only: an ITNS-281 challan records a
  deposit amount and a Financial Year, not a month or section, so "Under-deposited" /
  "Over-deposited" is the finest granularity possible from that data alone — it can flag a
  shortfall for the year, not which party or month caused it.
"""
    )

footer()
