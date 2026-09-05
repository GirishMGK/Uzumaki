"""Classification-logic table tests for GST ledgers -- the highest-value
tests in this pass since the naming-based heuristic is the main risk area.
Also covers the build_gst_summary()/build_month_pivot() aggregation with a
small fabricated set of ledger entries.
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest

from reports.common import classify_gst_ledger, resolve_ambiguous_direction
from reports.gst_summary import build_gst_summary, build_month_pivot, build_unclassified


@pytest.mark.parametrize(
    "name,parent,expected",
    [
        ("Output CGST", "Duties & Taxes", ("output", "CGST")),
        ("CGST Output", "Duties & Taxes", ("output", "CGST")),
        ("Input SGST", "Duties & Taxes", ("input", "SGST")),
        ("IGST Payable", "Duties & Taxes", ("output", "IGST")),
        ("Input Tax Credit - CGST", "Duties & Taxes", ("input", "CGST")),
        ("ITC IGST", "Duties & Taxes", ("input", "IGST")),
        ("CGST", "Duties & Taxes", ("ambiguous", "CGST")),
        ("Cess on Sales", "Duties & Taxes", ("ambiguous", "CESS")),
        ("UTGST Payable", "Duties & Taxes", ("output", "UTGST")),
        ("TDS Payable", "Duties & Taxes", (None, None)),
        ("Sales Account", "Sales Accounts", (None, None)),
        ("Cash", "Cash-in-Hand", (None, None)),
    ],
)
def test_classify_gst_ledger(name, parent, expected):
    assert classify_gst_ledger(name, parent) == expected


@pytest.mark.parametrize(
    "voucher_type,expected",
    [
        ("Sales", "output"),
        ("sales return", "output"),
        ("Credit Note", "output"),
        ("Purchase", "input"),
        ("Purchase Return", "input"),
        ("Debit Note", "input"),
        ("Journal", None),
        ("Payment", None),
        ("", None),
    ],
)
def test_resolve_ambiguous_direction(voucher_type, expected):
    assert resolve_ambiguous_direction(voucher_type) == expected


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Ledger Name": "Output CGST", "Date": datetime.date(2024, 1, 10), "Voucher Type": "Sales", "Debit": 0.0, "Credit": 900.0},
            {"Ledger Name": "CGST", "Date": datetime.date(2024, 1, 12), "Voucher Type": "Purchase", "Debit": 500.0, "Credit": 0.0},
            {"Ledger Name": "CGST", "Date": datetime.date(2024, 2, 5), "Voucher Type": "Journal", "Debit": 100.0, "Credit": 0.0},
            {"Ledger Name": "Cash", "Date": datetime.date(2024, 1, 10), "Voucher Type": "Sales", "Debit": 900.0, "Credit": 0.0},
        ]
    )


def _sample_ledger_master() -> dict:
    return {
        "Output CGST": {"group": "Duties & Taxes", "opening_balance": 0.0},
        "CGST": {"group": "Duties & Taxes", "opening_balance": 0.0},
        "Cash": {"group": "Cash-in-Hand", "opening_balance": 0.0},
    }


def test_build_gst_summary_direction_resolution():
    detail = build_gst_summary(_sample_df(), _sample_ledger_master())

    # Cash ledger entries are dropped entirely -- not a GST ledger at all.
    assert "Cash" not in set(detail["Ledger Name"])

    output_row = detail[(detail["Ledger Name"] == "Output CGST")].iloc[0]
    assert output_row["Direction"] == "Output"
    assert output_row["Tax Amount"] == 900.0

    jan_ambiguous = detail[(detail["Ledger Name"] == "CGST") & (detail["Month"] == "Jan-2024")].iloc[0]
    assert jan_ambiguous["Direction"] == "Input"  # resolved via the Purchase voucher type
    assert jan_ambiguous["Tax Amount"] == 500.0

    feb_ambiguous = detail[(detail["Ledger Name"] == "CGST") & (detail["Month"] == "Feb-2024")].iloc[0]
    assert feb_ambiguous["Direction"] == "Unclassified"  # Journal doesn't resolve a side


def test_build_month_pivot_and_unclassified():
    detail = build_gst_summary(_sample_df(), _sample_ledger_master())
    pivot = build_month_pivot(detail)

    jan = pivot[pivot["Month"] == "Jan-2024"].iloc[0]
    assert jan["Output CGST"] == 900.0
    assert jan["Input CGST"] == 500.0
    assert jan["Total Output"] == 900.0
    assert jan["Total Input"] == 500.0
    assert jan["Net GST Payable"] == 400.0

    # The Unclassified Feb-2024 row must never silently enter the payable pivot.
    assert "Feb-2024" not in set(pivot["Month"])

    unclassified = build_unclassified(detail)
    assert len(unclassified) == 1
    assert unclassified.iloc[0]["Month"] == "Feb-2024"


def test_build_gst_summary_empty_input():
    empty = pd.DataFrame(columns=["Ledger Name", "Date", "Voucher Type", "Debit", "Credit"])
    detail = build_gst_summary(empty, {})
    assert detail.empty
    assert build_month_pivot(detail).empty
    assert build_unclassified(detail).empty
