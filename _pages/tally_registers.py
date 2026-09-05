"""Hub page: Tally Sales & Purchase Register.

Item-wise (Stock Item, Quantity, Rate, Item Amount, plus each voucher's
overall value) rather than ledger-wise -- two tabs:

  - **Upload a JSON or XML export** — tally_tool/reports/sales_purchase_register.py's
    extract_register_from_export(), a second pass over the same Data
    Interchange export _pages/tally_extractions.py already reads for
    ledger-wise extraction, this time pulling the inventory-entry detail
    that was sitting unused in the same file.
  - **Connect to Tally (live)** — tally_connector.fetch_voucher_register(),
    unchanged, moved here from the Tally extraction tool page so that page
    doesn't keep growing tabs.

Both tabs render through one shared function so the output is identical
regardless of source. NOT YET VERIFIED AGAINST A REAL TALLY INSTANCE for
either path -- the live fetch was tested locally against a fabricated
response matching a real captured shape, and the file-based path has no
live Tally export to test against in this environment either.
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

from extract_ledgers import ensure_utf8
from reports.sales_purchase_register import extract_register_from_export
import tally_connector

page_header(
    "🧾", "Tally: Sales & Purchase Register",
    "Item-wise Sales/Purchase register — Stock Item, Quantity, Rate, Amount, plus each "
    "voucher's overall value — from an uploaded export or a live Tally connection.",
    badges=["Upload export or connect live", "Item-wise detail", "Voucher-level cross-check"],
)


def _render_register_results(df_reg: pd.DataFrame, register_type: str) -> None:
    if df_reg.empty:
        st.warning(f"No {register_type} vouchers found in this date range/company.")
        return

    unique_vouchers = df_reg.drop_duplicates(subset=["Voucher No", "Voucher GUID"])
    total_value = unique_vouchers["Voucher Total"].sum()

    st.divider()
    k1, k2, k3 = st.columns(3)
    k1.metric("Vouchers", len(unique_vouchers))
    k2.metric("Item lines", len(df_reg))
    k3.metric(f"Total {register_type} value", f"{total_value:,.2f}")

    st.dataframe(df_reg.head(500), use_container_width=True, hide_index=True)
    if len(df_reg) > 500:
        st.caption(f"Showing first 500 of {len(df_reg):,} rows — download the workbook for the full data.")

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_reg.to_excel(writer, sheet_name=f"{register_type} Register", index=False)
        voucher_summary = unique_vouchers[
            ["Date", "Voucher Type", "Voucher No", "Party Ledger", "Reference", "Voucher Total"]
        ]
        voucher_summary.to_excel(writer, sheet_name="Voucher Summary", index=False)
    st.download_button(
        f"⬇ Download {register_type} Register workbook",
        buf.getvalue(),
        file_name=f"tally_{register_type.lower()}_register.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

# ── Tab 1: upload a JSON or XML export ──────────────────────────────────────
with tab_upload:
    st.caption(
        "Same Data Interchange export the Tally extraction tool's Upload tab reads — "
        "this pulls the item-wise inventory detail out of it instead of the ledger entries."
    )
    uploaded = st.file_uploader("Tally export (JSON or XML)", type=["json", "xml"], key="reg_upload_file")

    register_type_u = st.radio("Register", ["Sales", "Purchase"], horizontal=True, key="register_type_upload")
    include_cancelled_u = st.checkbox(
        "Include cancelled/optional vouchers", value=False, key="reg_ic_upload"
    )

    if uploaded and st.button("Extract", type="primary", key="reg_extract_upload"):
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path = os.path.join(tmpdir, uploaded.name or "Transactions.json")
            with open(in_path, "wb") as f:
                f.write(uploaded.getvalue())

            try:
                with st.spinner("Checking encoding…"):
                    utf8_path = ensure_utf8(in_path)

                with st.spinner(f"Extracting {register_type_u} register from the export…"):
                    rows = extract_register_from_export(
                        utf8_path, {register_type_u}, include_cancelled=include_cancelled_u
                    )
            except Exception as e:
                st.error(f"Extraction failed: {e}")
                st.stop()

            _render_register_results(pd.DataFrame(rows), register_type_u)

# ── Tab 2: connect to a running Tally instance ──────────────────────────────
with tab_live:
    render_setup_help(expanded=False)
    st.caption(
        "Item-wise Sales/Purchase register pulled live from Tally — same XML/HTTP "
        "interface as the Tally extraction tool's Connect tab, so the same setup applies: "
        "Tally open locally, ODBC/XML Server enabled, both dates required."
    )

    host, port, company = render_connection_picker("tally_registers")

    register_type_l = st.radio("Register", ["Sales", "Purchase"], horizontal=True, key="register_type_live")

    c1, c2, c3 = st.columns(3)
    with c1:
        include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="reg_ic_live")
    with c2:
        from_date_l = st.date_input(
            "From date", value=datetime.date(2000, 1, 1), format="YYYY-MM-DD", key="reg_fd_live",
            min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1),
        )
    with c3:
        to_date_l = st.date_input(
            "To date", value=datetime.date.today(), format="YYYY-MM-DD", key="reg_td_live",
            min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1),
        )

    if st.button(f"Pull {register_type_l} Register", type="primary", key="reg_pull_live"):
        try:
            with st.spinner(f"Pulling {register_type_l} vouchers from Tally…"):
                rows = tally_connector.fetch_voucher_register(
                    host, port, company, {register_type_l},
                    from_date_l if isinstance(from_date_l, datetime.date) else None,
                    to_date_l if isinstance(to_date_l, datetime.date) else None,
                    include_cancelled=include_cancelled_l,
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
  Voucher No, Party Ledger, Reference, Narration, Stock Item, Quantity, Rate, Item Amount,
  plus the voucher's overall value (the GST-inclusive invoice total, from the non-party
  ledger entries) for a cross-check — same shape whether pulled from an upload or live.
- A service invoice with no stock items still gets a row, just without item-level detail.
- Cancelled/optional vouchers are excluded by default, matching Tally's own normal view.
- **Voucher Summary sheet** in the download — one row per unique voucher (Date, Type,
  No, Party, Reference, Voucher Total) for a quick tie-out against the invoice count/value
  you expect for the period.
- Need ledger-wise extraction (with running balance and cost centres) instead? See the
  **Tally extraction tool** page.
"""
    )

footer()
