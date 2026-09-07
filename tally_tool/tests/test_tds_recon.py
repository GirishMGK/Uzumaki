"""Phase 2: TDS deducted (books) vs deposited (challans) reconciliation."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from reports.tds_recon import build_tds_recon


def _tds_detail(rows):
    """rows: list of (month_label, tds_amount)."""
    return pd.DataFrame([{"Month": m, "TDS Amount": a} for m, a in rows])


def _challan(fy, amount):
    return {"Financial Year": fy, "Amount (Rs)": amount}


def test_matched():
    detail = _tds_detail([("Apr-2023", 1000.0), ("May-2023", 1000.0)])
    challans = [_challan("2023-24", "2,000")]
    result = build_tds_recon(detail, challans)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["Financial Year"] == "2023-24"
    assert row["TDS Deducted (Books)"] == 2000.0
    assert row["TDS Deposited (Challans)"] == 2000.0
    assert row["Status"] == "Matched"


def test_under_deposited():
    detail = _tds_detail([("Apr-2023", 5000.0)])
    challans = [_challan("2023-24", "3000")]
    result = build_tds_recon(detail, challans)
    row = result.iloc[0]
    assert row["Status"] == "Under-deposited"
    assert row["Difference"] == 2000.0


def test_over_deposited():
    detail = _tds_detail([("Apr-2023", 1000.0)])
    challans = [_challan("2023-24", "3000")]
    result = build_tds_recon(detail, challans)
    row = result.iloc[0]
    assert row["Status"] == "Over-deposited"
    assert row["Difference"] == -2000.0


def test_only_in_books():
    detail = _tds_detail([("Jan-2024", 500.0)])
    result = build_tds_recon(detail, [])
    row = result.iloc[0]
    assert row["Financial Year"] == "2023-24"  # Jan falls in the Apr(Y-1)-Mar(Y) FY
    assert row["Status"] == "Only in Books (no challan uploaded for this year)"


def test_only_in_challans():
    result = build_tds_recon(pd.DataFrame(columns=["Month", "TDS Amount"]), [_challan("2022-23", "1000")])
    row = result.iloc[0]
    assert row["Financial Year"] == "2022-23"
    assert row["Status"] == "Only in Challans (no TDS ledger activity found for this year)"


def test_fy_normalization_variants():
    """"2023-2024" and "2023-24" must normalize to the same FY key."""
    detail = _tds_detail([("Apr-2023", 1000.0)])
    challans = [_challan("2023-2024", "1000")]
    result = build_tds_recon(detail, challans)
    assert result.iloc[0]["Financial Year"] == "2023-24"
    assert result.iloc[0]["Status"] == "Matched"


def test_empty_inputs():
    result = build_tds_recon(pd.DataFrame(columns=["Month", "TDS Amount"]), [])
    assert result.empty
