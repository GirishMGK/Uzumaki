"""Classification-logic table tests for TDS ledgers, plus build_tds_summary()
covering party tracing (both direct and inferred) and the Nature of Payment
keyword lookup.
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest

from reports.common import classify_tds_ledger
from reports.tds_summary import build_tds_summary


@pytest.mark.parametrize(
    "name,expected",
    [
        ("TDS Payable - Contractors", (True, "194C - Contractors")),
        ("TDS on Professional Fees", (True, "194J - Professional/Technical Fees")),
        ("TDS on Rent", (True, "194I - Rent")),
        ("TDS Payable", (True, None)),
        ("TDS - 194C", (True, "194C - Contractors")),
        ("Sundry Creditors", (False, None)),
        ("Professional Charges", (False, None)),
    ],
)
def test_classify_tds_ledger(name, expected):
    assert classify_tds_ledger(name, "Duties & Taxes") == expected


def _ledger_master() -> dict:
    return {
        "TDS Payable - Contractors": {"group": "Duties & Taxes"},
        "ABC Contractors": {"group": "Sundry Creditors"},
        "Bank Account": {"group": "Bank Accounts"},
        "Cash": {"group": "Cash-in-Hand"},
        "Professional Charges": {"group": "Indirect Expenses"},
    }


def test_build_tds_summary_direct_party():
    df = pd.DataFrame(
        [
            {
                "Ledger Name": "ABC Contractors", "Date": datetime.date(2024, 1, 10),
                "Voucher Type": "Payment", "Voucher No": "PAY/1", "Party Ledger": "ABC Contractors",
                "Debit": 9000.0, "Credit": 0.0, "Voucher GUID": "g1", "Master ID": "1",
            },
            {
                "Ledger Name": "TDS Payable - Contractors", "Date": datetime.date(2024, 1, 10),
                "Voucher Type": "Payment", "Voucher No": "PAY/1", "Party Ledger": "ABC Contractors",
                "Debit": 0.0, "Credit": 1000.0, "Voucher GUID": "g1", "Master ID": "1",
            },
            {
                "Ledger Name": "Bank Account", "Date": datetime.date(2024, 1, 10),
                "Voucher Type": "Payment", "Voucher No": "PAY/1", "Party Ledger": "ABC Contractors",
                "Debit": 0.0, "Credit": 8000.0, "Voucher GUID": "g1", "Master ID": "1",
            },
        ]
    )
    detail = build_tds_summary(df, _ledger_master())

    assert len(detail) == 1
    row = detail.iloc[0]
    assert row["Party (Deductee)"] == "ABC Contractors"
    assert row["Nature of Payment"] == "194C - Contractors"
    assert row["TDS Amount"] == 1000.0
    assert row["Party Inferred"] == False  # noqa: E712 (pandas bool, not `is False`)
    assert row["PAN"] == ""


def test_build_tds_summary_infers_party_when_blank():
    """Party Ledger left blank on the voucher (e.g. some Journal entries) --
    should fall back to the largest non-TDS, non-cash/bank ledger entry on
    the same voucher, and flag the row as inferred."""
    df = pd.DataFrame(
        [
            {
                "Ledger Name": "Professional Charges", "Date": datetime.date(2024, 2, 5),
                "Voucher Type": "Journal", "Voucher No": "JV/1", "Party Ledger": "",
                "Debit": 9000.0, "Credit": 0.0, "Voucher GUID": "g2", "Master ID": "2",
            },
            {
                "Ledger Name": "TDS Payable - Contractors", "Date": datetime.date(2024, 2, 5),
                "Voucher Type": "Journal", "Voucher No": "JV/1", "Party Ledger": "",
                "Debit": 0.0, "Credit": 900.0, "Voucher GUID": "g2", "Master ID": "2",
            },
            {
                "Ledger Name": "Cash", "Date": datetime.date(2024, 2, 5),
                "Voucher Type": "Journal", "Voucher No": "JV/1", "Party Ledger": "",
                "Debit": 0.0, "Credit": 8100.0, "Voucher GUID": "g2", "Master ID": "2",
            },
        ]
    )
    detail = build_tds_summary(df, _ledger_master())

    assert len(detail) == 1
    row = detail.iloc[0]
    # Cash is excluded from inference candidates -- Professional Charges (the
    # larger remaining non-TDS entry) should be picked instead.
    assert row["Party (Deductee)"] == "Professional Charges"
    assert row["Party Inferred"] == True  # noqa: E712


def test_build_tds_summary_no_tds_ledgers_returns_empty():
    df = pd.DataFrame(
        [{"Ledger Name": "Cash", "Date": datetime.date(2024, 1, 1), "Voucher Type": "Receipt",
          "Voucher No": "R/1", "Party Ledger": "", "Debit": 100.0, "Credit": 0.0,
          "Voucher GUID": "g3", "Master ID": "3"}]
    )
    detail = build_tds_summary(df, _ledger_master())
    assert detail.empty
