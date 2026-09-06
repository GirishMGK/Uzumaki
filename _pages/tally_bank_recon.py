"""Hub page: Tally Bank Reconciliation Summary.

Book balance (from Tally's own bank ledger, as of a selected date) vs an
uploaded bank statement, matched by amount + a date-tolerance window,
presented as a standard Bank Reconciliation Statement (BRS). See
tally_tool/reports/bank_recon.py's module docstring for the sign
convention (positive = increases the bank balance, on both sides) and the
matching algorithm.

No new Tally XML shapes here (unlike Inventory Closing Stock) -- this reuses
the ledger extraction's own Running Balance column entirely, just re-sliced
to a specific date. The only new-ish surface is the bank statement upload/
column-mapping, which reuses je_audit_tool's file loader.
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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "je_audit_tool"))

from _pages.theme import page_header, footer
from _pages.tally_common import render_connection_picker, render_setup_help

from extract_ledgers import ensure_utf8, extract_any, build_tables
from reports.bank_recon import book_balance_as_of, build_brs, match_statement_to_books, normalize_statement
from utils.data_loader import load_single_file
import tally_connector

page_header(
    "🏦", "Tally: Bank Reconciliation",
    "Book balance from Tally vs an uploaded bank statement, as on a selected date — "
    "matched and presented as a standard BRS.",
    badges=["As on any date", "Upload your bank statement", "Standard BRS format"],
)


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
    """`key_prefix` namespaces every widget key -- this function is called
    from both the Upload and Live tabs, and once either tab has data in
    session_state it keeps rendering on every rerun (including reruns
    triggered by the *other* tab's widgets), so without a distinct prefix
    per tab, two calls in the same script run would create duplicate widget
    keys and Streamlit would raise."""
    bank_ledger = _pick_bank_ledger(ledger_master, key_prefix)
    if not bank_ledger:
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        as_of_date = st.date_input("As on date", value=datetime.date.today(), format="YYYY-MM-DD", key=f"{key_prefix}_as_of")
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
                "Statement-only tabs below for what might explain it, or whether the statement closing "
                "balance entered above is correct."
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
            "⬇ Download Bank Reconciliation workbook",
            buf.getvalue(),
            file_name="tally_bank_reconciliation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key_prefix}_download",
        )


tab_upload, tab_live = st.tabs(["📤 Upload export file", "🔌 Connect to Tally (live)"])

# ── Tab 1: upload a JSON or XML export ──────────────────────────────────────
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
                with st.spinner("Streaming the export…"):
                    ledger_master, rows = extract_any(utf8_path)
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

# ── Tab 2: connect to a running Tally instance ──────────────────────────────
with tab_live:
    render_setup_help(expanded=False)
    host, port, company = render_connection_picker("tally_brs")

    include_cancelled_l = st.checkbox("Include cancelled/optional vouchers", value=False, key="brs_ic_live")
    c1, c2 = st.columns(2)
    with c1:
        from_date_l = st.date_input(
            "From date", value=datetime.date(2000, 1, 1), format="YYYY-MM-DD", key="brs_fd_live",
            min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1),
        )
    with c2:
        to_date_l = st.date_input(
            "To date", value=datetime.date.today(), format="YYYY-MM-DD", key="brs_td_live",
            min_value=datetime.date(1990, 1, 1), max_value=datetime.date(2100, 1, 1),
        )

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
- **Balance as per Books** is read off the ledger extraction's own Running Balance column,
  sliced to the last transaction on or before the date you pick — the same running-balance
  logic the Tally extraction tool page already computes.
- **Sign convention**: on both sides, positive = increases the bank balance (a deposit/
  receipt), negative = decreases it (a withdrawal/payment) — this is deliberately *not* the
  bank's own Debit/Credit labels, which mean the opposite of the books' own Debit/Credit for
  a bank account. Map your statement's Deposit/Credit column as the "deposit" side regardless
  of what the bank itself calls it.
- **Matching**: same amount (within a paisa) and within your chosen date-tolerance window —
  closest date wins. Unmatched book entries are the classic reconciling items (a deposit not
  yet credited, a cheque not yet presented); unmatched statement entries are flagged for
  follow-up (bank charges, interest, a direct credit/debit never booked) rather than guessed at.
- **Statement closing balance** is a number you enter yourself (from the actual bank statement),
  not parsed from the uploaded transaction list — most statement exports don't reliably carry a
  running balance column, and guessing one would undermine the whole should-tie check. Leave it
  unconfirmed to see the reconciling items without the top-line arithmetic.
- **Match window**: only book/statement entries within this many days of the "As on" date are
  considered, so ancient unrelated transactions don't get pulled into the reconciliation.
"""
    )

footer()
