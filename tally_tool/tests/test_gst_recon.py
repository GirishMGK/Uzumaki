"""Phase 2: 3-way GST reconciliation (reports/gst_recon.py)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from reports.gst_recon import compute_recon_3way, resolve_gstin


def _month_pivot(rows):
    """rows: list of (month_label, total_output, total_input) -- month_label
    in Tally's "%b-%Y" shape, e.g. "Jan-2024"."""
    return pd.DataFrame(
        [{"Month": m, "Total Output": o, "Total Input": i} for m, o, i in rows]
    )


def _gstr1_row(gstin, period, taxable, igst=0.0, cgst=0.0, sgst=0.0):
    return {"GSTIN": gstin, "Tax Period": period, "TL Taxable Value": taxable,
            "TL IGST": igst, "TL CGST": cgst, "TL SGST": sgst}


def _gstr3b_row(gstin, period, taxable, igst=0.0, cgst=0.0, sgst=0.0):
    return {"GSTIN": gstin, "Tax Period": period, "3.1(a) Taxable Value": taxable,
            "3.1(a) IGST": igst, "3.1(a) CGST": cgst, "3.1(a) SGST": sgst}


def test_resolve_gstin_explicit_wins():
    gstin, note = resolve_gstin("27abcde1234f1z5", [_gstr1_row("27XYZ", "Jan", 100)], [])
    assert gstin == "27ABCDE1234F1Z5"
    assert note == ""


def test_resolve_gstin_auto_detects_single():
    gstin, note = resolve_gstin("", [_gstr1_row("27ABCDE1234F1Z5", "Jan", 100)], [_gstr3b_row("27ABCDE1234F1Z5", "Jan", 100)])
    assert gstin == "27ABCDE1234F1Z5"
    assert "auto-detected" in note


def test_resolve_gstin_ambiguous_with_multiple():
    gstin, note = resolve_gstin("", [_gstr1_row("27AAA", "Jan", 100)], [_gstr3b_row("27BBB", "Jan", 100)])
    assert gstin == ""
    assert "Multiple GSTINs" in note


def test_resolve_gstin_none_found():
    gstin, note = resolve_gstin("", [], [])
    assert gstin == ""
    assert "No GSTIN" in note


def test_compute_recon_3way_matched():
    # Note the join key is the bare month name ("January"), not "Jan-2024"
    # -- extract_gstr1()/extract_gstr3b() never carry a year in "Tax Period"
    # (see the module docstring), so compute_recon() itself keys that way.
    pivot = _month_pivot([("Jan-2024", 900.0, 0.0)])
    gstr1 = [_gstr1_row("27ABCDE1234F1Z5", "January", 5000.0, cgst=450.0, sgst=450.0)]
    gstr3b = [_gstr3b_row("27ABCDE1234F1Z5", "January", 5000.0, cgst=450.0, sgst=450.0)]

    result, collisions = compute_recon_3way(pivot, "27ABCDE1234F1Z5", gstr1, gstr3b)
    assert not collisions
    assert len(result) == 1
    row = result.iloc[0]
    assert row["Month"] == "January"
    assert row["Books Output Tax"] == 900.0
    assert row["GSTR-1 Tax (Output)"] == 900.0  # 450+450
    assert row["Status"] == "Matched"
    assert row["GSTR-1 vs GSTR-3B Status"] == "Matched"


def test_compute_recon_3way_books_vs_return_mismatch():
    pivot = _month_pivot([("Jan-2024", 500.0, 0.0)])  # books say only 500
    gstr1 = [_gstr1_row("27ABCDE1234F1Z5", "January", 5000.0, cgst=450.0, sgst=450.0)]  # return says 900
    gstr3b = [_gstr3b_row("27ABCDE1234F1Z5", "January", 5000.0, cgst=450.0, sgst=450.0)]

    result, _collisions = compute_recon_3way(pivot, "27ABCDE1234F1Z5", gstr1, gstr3b)
    row = result.iloc[0]
    assert row["Status"] == "Books vs Return Mismatch"
    assert row["Diff: Books vs GSTR-1"] == -400.0


def test_compute_recon_3way_only_in_books():
    pivot = _month_pivot([("Feb-2024", 300.0, 0.0)])
    result, _collisions = compute_recon_3way(pivot, "27ABCDE1234F1Z5", [], [])
    row = result.iloc[0]
    assert row["Month"] == "February"
    assert row["Status"] == "Only in Books (no filed return found for this month)"


def test_compute_recon_3way_only_in_filed_return():
    pivot = _month_pivot([])
    gstr1 = [_gstr1_row("27ABCDE1234F1Z5", "March", 1000.0, cgst=90.0, sgst=90.0)]
    gstr3b = [_gstr3b_row("27ABCDE1234F1Z5", "March", 1000.0, cgst=90.0, sgst=90.0)]
    result, _collisions = compute_recon_3way(pivot, "27ABCDE1234F1Z5", gstr1, gstr3b)
    assert len(result) == 1
    assert result.iloc[0]["Status"] == "Only in filed return"


def test_compute_recon_3way_flags_cross_year_collision():
    """Two calendar years' worth of "January" in the same Tally pull --
    summed together, but flagged rather than silently merged."""
    pivot = _month_pivot([("Jan-2023", 400.0, 0.0), ("Jan-2024", 500.0, 0.0)])
    result, collisions = compute_recon_3way(pivot, "27ABCDE1234F1Z5", [], [])
    assert collisions == {"January"}
    row = result.iloc[0]
    assert row["Books Output Tax"] == 900.0  # summed across both years
