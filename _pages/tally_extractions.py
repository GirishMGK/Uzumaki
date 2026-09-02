"""Hub page: Tally extraction tool.

Pulls every ledger's full transaction history out of Tally in one shot,
instead of opening and exporting each ledger one by one. Two ways in:

  - **Upload a JSON export** — wraps tally_tool/extract_ledgers.py's real
    functions directly (encoding detection, streaming parse, running-balance
    and control-total logic), same as the standalone CLI.
  - **Connect to Tally (live)** — wraps tally_tool/tally_connector.py, which
    pulls the same data straight from a running TallyPrime instance over its
    XML/HTTP interface, no manual export step. Requires Tally to be open
    locally with ODBC/XML Server enabled (F12 -> Advanced Configuration) --
    see the "Connect to Tally" tab for details. NOTE: the live-connect path
    has not been exercised against a real Tally instance (this environment
    has none to test with) -- expect to iterate after trying it for real.

Both paths converge on the same build_tables()/write_output() and
control-total display, so the output workbook is identical either way.
"""
import datetime
import os
import sys
import tempfile

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tally_tool"))

from _pages.theme import page_header, footer

from extract_ledgers import ensure_utf8, extract_any, build_tables, write_output
import tally_connector

page_header(
    "📒", "Tally extraction tool",
    "Pull every ledger's full transaction history out of Tally in one shot — "
    "no more opening and exporting each ledger one by one.",
    badges=["Upload export or connect live", "Running balance per ledger", "Control-total check"],
)


def _render_results(df, summary, tmpdir):
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
        )


tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

# ── Tab 1: upload a JSON or XML export ──────────────────────────────────────
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

    uploaded = st.file_uploader("Tally export (JSON or XML)", type=["json", "xml"])

    c1, c2, c3 = st.columns(3)
    with c1:
        include_cancelled_u = st.checkbox("Include cancelled/optional vouchers", value=False, key="ic_upload")
    with c2:
        from_date_u = st.date_input("From date (optional)", value=None, format="YYYY-MM-DD", key="fd_upload")
    with c3:
        to_date_u = st.date_input("To date (optional)", value=None, format="YYYY-MM-DD", key="td_upload")

    ledger_filter_u = st.text_input(
        "Only these ledgers (optional — exact names, comma-separated)",
        placeholder="e.g. Cash, ABC Traders",
        key="lf_upload",
    )

    if uploaded and st.button("Extract", type="primary", key="extract_upload"):
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path = os.path.join(tmpdir, uploaded.name or "Transactions.json")
            with open(in_path, "wb") as f:
                f.write(uploaded.getvalue())

            try:
                with st.spinner("Checking encoding…"):
                    utf8_path = ensure_utf8(in_path)

                with st.spinner("Streaming the export (this can take a while for large files)…"):
                    ledger_master, rows = extract_any(utf8_path)

                with st.spinner("Building ledger tables and running balances…"):
                    df, summary = build_tables(
                        ledger_master, rows,
                        include_cancelled=include_cancelled_u,
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

            _render_results(df, summary, tmpdir)

# ── Tab 2: connect to a running Tally instance ──────────────────────────────
with tab_live:
    with st.expander("Setup (one-time per Tally session)", expanded=True):
        st.markdown(
            """
1. Restore/open the backup **inside TallyPrime itself** — nothing outside Tally
   can read its backup format directly.
2. **F12** (Configure) → **Advanced Configuration** → enable **ODBC/XML Server**
   (older versions: "Client/Server Configuration"), note the port (default **9000**).
3. Keep Tally open with the company loaded for the duration of the pull.
4. Uzumaki and Tally must be on the **same machine** (or reachable over the network) —
   click **Test Connection** below once Tally is ready.

⚠️ This live-connect path talks to Tally's XML/HTTP interface directly and has not yet
been run against a real Tally instance in development — if something doesn't come back
right (empty fields, connection errors), that's expected on the first try; report it back.
"""
        )

    c1, c2 = st.columns([3, 1])
    with c1:
        host = st.text_input("Tally host", value="localhost")
    with c2:
        port = st.number_input("Port", value=tally_connector.DEFAULT_PORT, min_value=1, max_value=65535, step=1)

    if "tally_companies" not in st.session_state:
        st.session_state.tally_companies = []

    if st.button("Test Connection"):
        with st.spinner("Contacting Tally…"):
            ok, message = tally_connector.test_connection(host, int(port))
        if ok:
            st.success(message)
            try:
                st.session_state.tally_companies = tally_connector.list_companies(host, int(port))
            except tally_connector.TallyConnectionError:
                st.session_state.tally_companies = []
        else:
            st.error(message)
            st.session_state.tally_companies = []

    company = None
    if st.session_state.tally_companies:
        company = st.selectbox("Company", st.session_state.tally_companies)
    else:
        company = st.text_input(
            "Company name (optional — leave blank to use whichever company is currently open)",
            key="company_manual",
        ) or None

    c1, c2, c3 = st.columns(3)
    with c1:
        include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="ic_live")
    with c2:
        from_date_l = st.date_input("From date", value=None, format="YYYY-MM-DD", key="fd_live")
    with c3:
        to_date_l = st.date_input("To date", value=None, format="YYYY-MM-DD", key="td_live")

    ledger_filter_l = st.text_input(
        "Only these ledgers (optional — exact names, comma-separated)",
        placeholder="e.g. Cash, ABC Traders",
        key="lf_live",
    )

    if st.button("Pull from Tally", type="primary", key="pull_live"):
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
                        ledger_master, rows,
                        include_cancelled=include_cancelled_l,
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

            _render_results(df, summary, tmpdir)

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
- **Live connect** pulls the same fields directly from a running Tally instance over its
  XML/HTTP interface — no manual export step, but Tally must be open locally with
  ODBC/XML Server enabled.

**Command line** (for scripting/large batches):
```bash
python tally_tool/extract_ledgers.py --input "Transactions.json" --output "ledgers_output.xlsx"
```
"""
    )

footer()
