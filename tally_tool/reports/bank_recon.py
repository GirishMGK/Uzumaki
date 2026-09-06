"""Bank Reconciliation Summary -- book balance (from Tally) vs an uploaded
bank statement, as on a selected date.

SIGN CONVENTION
-----------------
Both sides of a reconciliation use one signed "Amount" convention:
**positive = increases the bank balance** (a deposit/receipt), **negative =
decreases it** (a withdrawal/payment/cheque issued). On the books side this
is exactly extract_ledgers.py's own Debit-minus-Credit ("Net") convention
for an asset ledger, so no re-derivation is needed there. On the bank
statement side, a bank's own "Credit" column (money credited to the
customer's account = a deposit) maps to the SAME positive sign, and a
bank's "Debit" column (a withdrawal) maps to negative -- despite "Debit"/
"Credit" meaning opposite things on a bank statement vs in the entity's own
books, the sign convention here is aligned by what it does to the balance,
not by the label, which is exactly the classic bank-reconciliation
gotcha this avoids getting backwards. normalize_statement() below is where
a statement's own Debit/Credit or Withdrawal/Deposit columns get mapped
into this convention.

MATCHING
--------
match_statement_to_books() does a greedy one-to-one match: same amount
(within a paisa) and within a date-tolerance window, closest date wins.
Unmatched book rows are the classic reconciling items (a receipt banked but
not yet cleared, a cheque issued but not yet presented); unmatched
statement rows are flagged for follow-up (bank charges, interest, a direct
credit/debit never booked) rather than guessed at.
"""

from __future__ import annotations

import datetime

import pandas as pd

STATEMENT_COLUMNS = ["Date", "Amount", "Reference"]
MATCH_COLUMNS = ["Book Date", "Statement Date", "Date Diff (days)", "Amount", "Reference"]


def book_balance_as_of(df: pd.DataFrame, ledger_name: str, as_of_date: datetime.date) -> tuple[float, datetime.date | None]:
    """Balance of `ledger_name` as of `as_of_date`, read off the ledger
    extraction's own Running Balance column (extract_ledgers.build_tables()
    output, called with no date filtering so the running balance -- computed
    cumulatively over the FULL period -- stays correct; this function does
    its own as-of-date slicing on top).

    Returns (balance, last_transaction_date_used). last_transaction_date_used
    is None when the ledger has no transactions on/before as_of_date at all
    -- the balance returned is then that ledger's Opening Balance."""
    ledger_rows = df[df["Ledger Name"] == ledger_name]
    if ledger_rows.empty:
        return 0.0, None

    on_or_before = ledger_rows[ledger_rows["Date"].apply(lambda d: d is not None and d <= as_of_date)]
    if on_or_before.empty:
        opening = float(ledger_rows.iloc[0]["Opening Balance"])
        return opening, None

    # Rows are already sorted (Ledger Name, Date, _seq) by build_tables() --
    # the last one on/before as_of_date carries the correct cumulative balance.
    last_row = on_or_before.iloc[-1]
    return float(last_row["Running Balance"]), last_row["Date"]


def normalize_statement(
    df: pd.DataFrame,
    date_col: str,
    deposit_col: str | None = None,
    withdrawal_col: str | None = None,
    amount_col: str | None = None,
    reference_col: str | None = None,
) -> pd.DataFrame:
    """Maps an uploaded bank statement's own columns onto the standard
    (Date, Amount, Reference) shape, Amount signed per the module's
    convention (positive = deposit). Pass either (deposit_col AND/OR
    withdrawal_col) for statements that split the two, or amount_col for a
    statement with one already-signed column (positive assumed = deposit;
    flip the column yourself first if a given export signs it the other way)."""
    working = pd.DataFrame()
    working["Date"] = pd.to_datetime(df[date_col], errors="coerce").dt.date

    if amount_col:
        working["Amount"] = pd.to_numeric(df[amount_col], errors="coerce").fillna(0.0)
    else:
        deposits = pd.to_numeric(df[deposit_col], errors="coerce").fillna(0.0) if deposit_col else 0.0
        withdrawals = pd.to_numeric(df[withdrawal_col], errors="coerce").fillna(0.0) if withdrawal_col else 0.0
        working["Amount"] = deposits - withdrawals

    working["Reference"] = df[reference_col].astype(str) if reference_col else ""
    working = working.dropna(subset=["Date"]).reset_index(drop=True)
    return working[STATEMENT_COLUMNS]


def match_statement_to_books(
    book_df: pd.DataFrame, stmt_df: pd.DataFrame, date_tolerance_days: int = 7
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """book_df/stmt_df: both need "Date" (datetime.date) and "Amount"
    (signed per the module convention) columns; book_df additionally
    carries whatever reference/voucher columns the caller wants echoed back
    in book_only's output (passed through unchanged).

    Greedy match: for each book row (in order), the closest-dated unclaimed
    statement row with the same amount (within 1 paisa) and within
    date_tolerance_days is claimed. Returns (matched, book_only, stmt_only)."""
    book = book_df.reset_index(drop=True).copy()
    stmt = stmt_df.reset_index(drop=True).copy()
    book["_matched"] = False
    stmt["_matched"] = False

    matches = []
    for bi in range(len(book)):
        brow = book.iloc[bi]
        candidates = stmt[(~stmt["_matched"]) & ((stmt["Amount"] - brow["Amount"]).abs() <= 0.01)]
        if candidates.empty:
            continue
        diffs = candidates["Date"].apply(lambda d: abs((d - brow["Date"]).days))
        within = candidates[diffs <= date_tolerance_days]
        if within.empty:
            continue
        best_idx = diffs[within.index].idxmin()
        stmt.loc[best_idx, "_matched"] = True
        book.loc[bi, "_matched"] = True
        matches.append({
            "Book Date": brow["Date"],
            "Statement Date": stmt.loc[best_idx, "Date"],
            "Date Diff (days)": abs((stmt.loc[best_idx, "Date"] - brow["Date"]).days),
            "Amount": brow["Amount"],
            "Reference": brow.get("Reference", ""),
        })

    matched_df = pd.DataFrame(matches, columns=MATCH_COLUMNS)
    book_only = book[~book["_matched"]].drop(columns=["_matched"]).reset_index(drop=True)
    stmt_only = stmt[~stmt["_matched"]].drop(columns=["_matched"]).reset_index(drop=True)
    return matched_df, book_only, stmt_only


def build_brs(
    book_only: pd.DataFrame,
    stmt_only: pd.DataFrame,
    book_balance: float,
    statement_closing_balance: float | None,
) -> dict:
    """Standard Bank Reconciliation Statement schedule, starting from the
    bank statement's own closing balance (if given -- see the page for why
    this is a plain user-entered number rather than parsed from the
    statement) and working to what that implies the book balance should be,
    for a should-tie check against the actual book_balance.

    Returns a dict of line items (amount, description) plus the computed
    vs actual book balance and whether they tie within a paisa -- None for
    the computed/tie fields when statement_closing_balance wasn't given
    (the reconciling items below are still meaningful on their own)."""
    deposits_not_credited = float(book_only[book_only["Amount"] > 0]["Amount"].sum()) if not book_only.empty else 0.0
    cheques_not_presented = float(-book_only[book_only["Amount"] < 0]["Amount"].sum()) if not book_only.empty else 0.0
    stmt_only_net = float(stmt_only["Amount"].sum()) if not stmt_only.empty else 0.0

    result = {
        "statement_closing_balance": statement_closing_balance,
        "deposits_not_credited": deposits_not_credited,
        "cheques_not_presented": cheques_not_presented,
        "items_in_statement_not_in_books": stmt_only_net,
        "book_balance": book_balance,
        "computed_book_balance": None,
        "tie_difference": None,
        "ties": None,
    }
    if statement_closing_balance is not None:
        # Derivation (see module docstring for the sign convention): the
        # statement already reflects items the bank has applied that the
        # books haven't recorded yet, so book balance TRAILS the statement
        # by that same signed amount -- a bank charge (negative) means the
        # books are relatively HIGHER (haven't caught the deduction yet),
        # hence subtracting stmt_only_net (a negative charge subtracts a
        # negative, i.e. adds it back); an uncredited interest amount
        # (positive) means the books are relatively LOWER by that much.
        computed = statement_closing_balance + deposits_not_credited - cheques_not_presented - stmt_only_net
        result["computed_book_balance"] = computed
        diff = round(computed - book_balance, 2)
        result["tie_difference"] = diff
        result["ties"] = abs(diff) <= 1.0
    return result
