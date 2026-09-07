"""Bank Reconciliation (reports/bank_recon.py) -- book balance as-of-date,
statement normalization, matching, and the BRS arithmetic. The BRS sign
convention is the highest-risk part of this module (the classic bank-recon
"whose Debit is whose Credit" trap), so it gets dedicated, worked-example
tests for each reconciling-item type, not just round-trip checks.
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from reports.bank_recon import book_balance_as_of, build_brs, match_statement_to_books, normalize_statement


def _ledger_df(rows):
    """rows: list of (ledger_name, date, running_balance, opening_balance)."""
    return pd.DataFrame([
        {"Ledger Name": n, "Date": d, "Running Balance": rb, "Opening Balance": ob}
        for n, d, rb, ob in rows
    ])


def test_book_balance_as_of_uses_last_row_on_or_before_date():
    df = _ledger_df([
        ("HDFC Bank", datetime.date(2024, 1, 5), 1000.0, 500.0),
        ("HDFC Bank", datetime.date(2024, 1, 20), 1500.0, 500.0),
        ("HDFC Bank", datetime.date(2024, 2, 1), 2000.0, 500.0),
    ])
    balance, last_date = book_balance_as_of(df, "HDFC Bank", datetime.date(2024, 1, 25))
    assert balance == 1500.0
    assert last_date == datetime.date(2024, 1, 20)


def test_book_balance_as_of_falls_back_to_opening_balance():
    df = _ledger_df([("HDFC Bank", datetime.date(2024, 2, 1), 2000.0, 500.0)])
    balance, last_date = book_balance_as_of(df, "HDFC Bank", datetime.date(2024, 1, 1))
    assert balance == 500.0
    assert last_date is None


def test_book_balance_as_of_unknown_ledger():
    df = _ledger_df([("HDFC Bank", datetime.date(2024, 1, 1), 1000.0, 500.0)])
    balance, last_date = book_balance_as_of(df, "ICICI Bank", datetime.date(2024, 1, 1))
    assert balance == 0.0
    assert last_date is None


def test_normalize_statement_with_deposit_withdrawal_columns():
    raw = pd.DataFrame({
        "Txn Date": ["2024-01-05", "2024-01-06"],
        "Deposit": [1000, 0],
        "Withdrawal": [0, 400],
        "Particulars": ["Cheque dep", "ATM wdl"],
    })
    out = normalize_statement(raw, date_col="Txn Date", deposit_col="Deposit", withdrawal_col="Withdrawal", reference_col="Particulars")
    assert out.loc[0, "Amount"] == 1000.0
    assert out.loc[1, "Amount"] == -400.0
    assert out.loc[0, "Date"] == datetime.date(2024, 1, 5)


def test_normalize_statement_with_signed_amount_column():
    raw = pd.DataFrame({"Date": ["2024-01-05"], "Amt": [-250]})
    out = normalize_statement(raw, date_col="Date", amount_col="Amt")
    assert out.loc[0, "Amount"] == -250.0


def test_match_statement_to_books_exact_and_near_date():
    book = pd.DataFrame([
        {"Date": datetime.date(2024, 1, 5), "Amount": 1000.0, "Reference": "Cheque 001"},
        {"Date": datetime.date(2024, 1, 10), "Amount": -400.0, "Reference": "Cheque 002"},
    ])
    stmt = pd.DataFrame([
        {"Date": datetime.date(2024, 1, 5), "Amount": 1000.0, "Reference": ""},   # exact match
        {"Date": datetime.date(2024, 1, 14), "Amount": -400.0, "Reference": ""},  # 4-day-late clearance
    ])
    matched, book_only, stmt_only = match_statement_to_books(book, stmt, date_tolerance_days=7)
    assert len(matched) == 2
    assert book_only.empty
    assert stmt_only.empty
    late = matched[matched["Date Diff (days)"] > 0].iloc[0]
    assert late["Date Diff (days)"] == 4


def test_match_statement_to_books_outside_tolerance_stays_unmatched():
    book = pd.DataFrame([{"Date": datetime.date(2024, 1, 1), "Amount": 500.0, "Reference": "Deposit"}])
    stmt = pd.DataFrame([{"Date": datetime.date(2024, 1, 20), "Amount": 500.0, "Reference": ""}])
    matched, book_only, stmt_only = match_statement_to_books(book, stmt, date_tolerance_days=7)
    assert matched.empty
    assert len(book_only) == 1
    assert len(stmt_only) == 1


def test_match_statement_to_books_unmatched_deposit_and_cheque():
    book = pd.DataFrame([
        {"Date": datetime.date(2024, 1, 30), "Amount": 1000.0, "Reference": "Deposit in transit"},
        {"Date": datetime.date(2024, 1, 28), "Amount": -400.0, "Reference": "Cheque outstanding"},
    ])
    stmt = pd.DataFrame(columns=["Date", "Amount", "Reference"])
    matched, book_only, stmt_only = match_statement_to_books(book, stmt)
    assert matched.empty
    assert len(book_only) == 2
    assert stmt_only.empty


# ── BRS arithmetic: one worked example per reconciling-item type ───────────

def test_brs_deposit_in_transit():
    """Statement 950; a 100 deposit is recorded in books but not yet
    credited by the bank -- book balance should be 1050."""
    book_only = pd.DataFrame([{"Amount": 100.0}])
    stmt_only = pd.DataFrame(columns=["Amount"])
    result = build_brs(book_only, stmt_only, book_balance=1050.0, statement_closing_balance=950.0)
    assert result["deposits_not_credited"] == 100.0
    assert result["cheques_not_presented"] == 0.0
    assert result["computed_book_balance"] == 1050.0
    assert result["ties"] is True


def test_brs_cheque_not_presented():
    """Statement 1000; a 200 payment is recorded in books but the cheque
    hasn't been presented yet -- book balance should be 800."""
    book_only = pd.DataFrame([{"Amount": -200.0}])
    stmt_only = pd.DataFrame(columns=["Amount"])
    result = build_brs(book_only, stmt_only, book_balance=800.0, statement_closing_balance=1000.0)
    assert result["cheques_not_presented"] == 200.0
    assert result["computed_book_balance"] == 800.0
    assert result["ties"] is True


def test_brs_bank_charge_not_yet_booked():
    """Bank charged 50 (reflected in the 950 statement balance already);
    books haven't recorded the charge yet, so book balance is still 1000 --
    the classic sign trap this module's docstring calls out."""
    book_only = pd.DataFrame(columns=["Amount"])
    stmt_only = pd.DataFrame([{"Amount": -50.0}])
    result = build_brs(book_only, stmt_only, book_balance=1000.0, statement_closing_balance=950.0)
    assert result["items_in_statement_not_in_books"] == -50.0
    assert result["computed_book_balance"] == 1000.0
    assert result["ties"] is True


def test_brs_interest_credited_not_yet_booked():
    """Bank credited 30 interest (reflected in the 980 statement balance);
    books haven't recorded it yet, so book balance is still 950."""
    book_only = pd.DataFrame(columns=["Amount"])
    stmt_only = pd.DataFrame([{"Amount": 30.0}])
    result = build_brs(book_only, stmt_only, book_balance=950.0, statement_closing_balance=980.0)
    assert result["computed_book_balance"] == 950.0
    assert result["ties"] is True


def test_brs_flags_genuine_mismatch():
    result = build_brs(pd.DataFrame(columns=["Amount"]), pd.DataFrame(columns=["Amount"]), book_balance=999.0, statement_closing_balance=950.0)
    assert result["ties"] is False
    assert result["tie_difference"] == -49.0


def test_brs_without_statement_closing_balance():
    book_only = pd.DataFrame([{"Amount": 100.0}])
    result = build_brs(book_only, pd.DataFrame(columns=["Amount"]), book_balance=1050.0, statement_closing_balance=None)
    assert result["computed_book_balance"] is None
    assert result["ties"] is None
    assert result["deposits_not_credited"] == 100.0
