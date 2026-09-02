"""Hub page: Tally extraction tool.

Pulls every ledger's full transaction history out of a Tally JSON (Data
Interchange) export in one shot, instead of opening and exporting each
ledger one by one. Wraps tally_tool/extract_ledgers.py's real functions
directly — same encoding detection, streaming parse, running-balance and
control-total logic as the standalone CLI.
"""
import datetime
import io
import os
import sys
import tempfile

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tally_tool"))

from _pages.theme import page_header, footer

from extract_ledgers import ensure_utf8, extract, build_tables, write_output

page_header(
    "📒", "Tally extraction tool",
    "Pull every ledger's full transaction history out of a Tally export in one shot — "
    "no more opening and exporting each ledger one by one.",
    badges=["Streams large exports", "Running balance per ledger", "Control-total check"],
)

with st.expander("How to export from Tally", expanded=False):
    st.markdown(
        """
1. **Gateway of Tally → Display → Day Book** (or any report/period covering everything you need)
2. **Alt+F2** → set the date range to the full period (e.g. the full financial year)
3. **F12** (Configure) → make sure narrations and full ledger-entry detail are shown
4. **Alt+E** (Export) → Format: **JSON (Data Interchange)** → Yes to "Export All"

This single file contains every **Ledger master** and every **Voucher** with its full
ledger-entry detail — everything this tool needs.
"""
    )

uploaded = st.file_uploader("Tally JSON export", type=["json"])

c1, c2, c3 = st.columns(3)
with c1:
    include_cancelled = st.checkbox("Include cancelled/optional vouchers", value=False)
with c2:
    from_date = st.date_input("From date (optional)", value=None, format="YYYY-MM-DD")
with c3:
    to_date = st.date_input("To date (optional)", value=None, format="YYYY-MM-DD")

ledger_filter = st.text_input(
    "Only these ledgers (optional — exact names, comma-separated)",
    placeholder="e.g. Cash, ABC Traders",
)

if uploaded and st.button("Extract", type="primary"):
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = os.path.join(tmpdir, uploaded.name or "Transactions.json")
        with open(in_path, "wb") as f:
            f.write(uploaded.getvalue())

        try:
            with st.spinner("Checking encoding…"):
                utf8_path = ensure_utf8(in_path)

            with st.spinner("Streaming the export (this can take a while for large files)…"):
                ledger_master, rows = extract(utf8_path)

            with st.spinner("Building ledger tables and running balances…"):
                df, summary = build_tables(
                    ledger_master, rows,
                    include_cancelled=include_cancelled,
                    from_date=from_date if isinstance(from_date, datetime.date) else None,
                    to_date=to_date if isinstance(to_date, datetime.date) else None,
                    ledger_filter=ledger_filter or None,
                )
        except SystemExit as e:
            st.error(str(e))
            st.stop()
        except Exception as e:
            st.error(f"Extraction failed: {e}")
            st.stop()

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
                "double-check the export covers a full period with 'Export All'."
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
            )

with st.expander("What this does"):
    st.markdown(
        """
- **Transactions sheet** — one row per ledger entry: Ledger Name, Ledger Group, Date,
  Voucher Type, Voucher No, Reference, Party Ledger, Narration, Debit, Credit,
  Opening Balance, Running Balance (+ Dr/Cr label), Bill Reference, Voucher GUID, Master ID.
- **Ledger Summary sheet** — one row per ledger: Group, Opening Balance, Total Debit,
  Total Credit, Closing Balance, Transaction Count — use this to tie out against your
  trial balance.
- **Debit/Credit** is taken from the **sign of Tally's `amount` field**, not the
  `isdeemedpositive` flag — the flag was found unreliable on some statutory/duty ledger
  entries (e.g. TDS lines on vouchers migrated from an older Tally version).
- Cancelled and optional (memo) vouchers are excluded by default, matching what Tally
  itself shows in a normal ledger view.
- Large exports (hundreds of MB) are streamed, not loaded whole into memory; a UTF-8
  cache of the export is kept alongside the upload only for this session.

**Command line** (for scripting/large batches):
```bash
python tally_tool/extract_ledgers.py --input "Transactions.json" --output "ledgers_output.xlsx"
```
"""
    )

footer()
