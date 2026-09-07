"""Hub page: Tally Inventory Closing Stock.

Item-wise opening/closing quantity and value. Tally's own StockItem master
carries a "closing" figure, but it's Tally's *current-period* number, not
necessarily as of the date the user actually wants -- so this page derives
an as-of-date closing (Opening + Purchases − Sales, from the Sales/Purchase
Register's item-wise movements) and compares it against Tally's own
reported closing as a should-tie check. See
tally_tool/reports/inventory_stock.py's module docstring for the documented
limitations (Stock Journal/Manufacturing Journal movements aren't captured;
Sales/Purchase Return and Credit/Debit Note lines aren't netted) -- expect
variance wherever those apply, and treat the variance column as the actual
audit-relevant signal, not noise to chase to zero.

Uses tally_connector.fetch_stock_items() -- a brand-new COLLECTION TYPE
(StockItem) for this codebase, higher live-verification risk than the
Ledger/Voucher collections already used elsewhere. NOT YET VERIFIED AGAINST
A REAL TALLY INSTANCE. The file-based upload path is similarly unverified
-- it's genuinely unclear without a real export whether Tally's JSON/XML
Data Interchange "Export All" includes Stock Item masters at all; if it
doesn't, extract_stock_items_from_export() comes back empty and the page
says so rather than erroring.
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
from reports.inventory_stock import build_negative_stock_flags, build_stock_summary, extract_stock_items_from_export
from reports.sales_purchase_register import extract_register_from_export
import tally_connector

page_header(
    "📦", "Tally: Inventory Closing Stock",
    "Item-wise closing quantity and value — Tally's own reported closing vs a derived "
    "figure from Sales/Purchase movements, with the variance flagged for review.",
    badges=["Opening + Purchases − Sales", "Variance vs Tally's own closing", "Negative-stock red flag"],
)


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
            f"{len(negative)} item(s) show a negative closing quantity — a classic red flag "
            "(physically impossible without an unrecorded purchase, a fabricated sale, or a "
            "costing/data error). See the Negative Stock tab."
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
                "Journal vouchers (not captured by this pass) or has material Sales/Purchase "
                "Returns (deliberately not netted — see \"What this does\" below)."
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
        "⬇ Download Inventory Closing Stock workbook",
        buf.getvalue(),
        file_name="tally_inventory_closing_stock.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

# ── Tab 1: upload a JSON or XML export ──────────────────────────────────────
with tab_upload:
    st.caption(
        "Same Data Interchange export the other Tally pages read — stock item masters and "
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
                    # No ledger_master passed -- this page doesn't need the
                    # per-voucher GST breakup extract_register_from_export()
                    # can optionally compute, just the item quantities.
                    sales_rows = extract_register_from_export(
                        utf8_path, {"Sales"}, include_cancelled=include_cancelled_u
                    )
                    purchase_rows = extract_register_from_export(
                        utf8_path, {"Purchase"}, include_cancelled=include_cancelled_u
                    )
            except Exception as e:
                st.error(f"Extraction failed: {e}")
                st.stop()

            summary = build_stock_summary(stock_items, sales_rows + purchase_rows)
            _render_stock_results(summary)

# ── Tab 2: connect to a running Tally instance ──────────────────────────────
with tab_live:
    render_setup_help(expanded=False)
    st.caption(
        "Pulls the Stock Item master (opening figures + Tally's own reported closing) plus the "
        "Sales and Purchase registers for the period (item movements) — same XML/HTTP interface "
        "as the other live-pull pages."
    )

    host, port, company = render_connection_picker("tally_inventory")

    c1, c2, c3 = st.columns(3)
    with c1:
        include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="inv_ic_live")
    with c2:
        from_date_l = st.date_input(
            "From date (start of the movement period)", value=datetime.date(2000, 1, 1), format="YYYY-MM-DD",
            key="inv_fd_live", min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1),
        )
    with c3:
        to_date_l = st.date_input(
            "As on date", value=datetime.date.today(), format="YYYY-MM-DD",
            key="inv_td_live", min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1),
        )

    if st.button("Pull Inventory Closing Stock", type="primary", key="inv_pull_live"):
        try:
            with st.spinner("Pulling stock item masters from Tally…"):
                stock_items = tally_connector.fetch_stock_items(host, port, company)
            with st.spinner("Pulling Sales register movements…"):
                sales_rows = tally_connector.fetch_voucher_register(
                    host, port, company, {"Sales"}, from_date_l, to_date_l,
                    include_cancelled=include_cancelled_l,
                )
            with st.spinner("Pulling Purchase register movements…"):
                purchase_rows = tally_connector.fetch_voucher_register(
                    host, port, company, {"Purchase"}, from_date_l, to_date_l,
                    include_cancelled=include_cancelled_l,
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
  StockItem master. The reported closing is Tally's *current-period* figure — not necessarily
  as of the "As on date" you picked, which is exactly why this page also derives one.
- **Derived Closing Qty** = Opening + Purchases Qty (in the period) − Sales Qty (in the period),
  using the Sales/Purchase Register's item-wise quantities.
- **Variance Qty** = Tally Reported − Derived. This is the audit-relevant signal, not a bug to
  chase to zero — persistent variance is *expected* wherever the company uses Stock Journal or
  Manufacturing Journal vouchers (not fetched in this pass) for internal transfers/consumption.
- **Sales Return, Purchase Return, Credit Note, and Debit Note** voucher lines are deliberately
  **not** netted into the derived figure — their quantity sign convention in Tally's own export
  isn't verified against a real instance, and guessing wrong would silently corrupt the number.
  Expect extra variance for companies with material returns.
- **Negative Stock tab** — any item with a negative Derived or Tally-reported closing quantity.
  Physically impossible without a data or process problem (unrecorded purchase, fabricated
  sale, costing error) — a classic forensic red flag.
- **Upload tab**: if the export doesn't contain any Stock Item masters (unconfirmed whether
  Tally's JSON/XML "Export All" includes them), you'll see a message saying so rather than an
  error — use **Connect to Tally (live)** instead in that case.
"""
    )

footer()
