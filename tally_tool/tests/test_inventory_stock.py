"""Inventory closing stock (reports/inventory_stock.py) -- derived closing
qty from Opening + Purchases - Sales, vs Tally's own reported closing."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reports.inventory_stock import build_negative_stock_flags, build_stock_summary


def _stock_item(name, opening_qty=0.0, opening_value=0.0, closing_qty=0.0, closing_value=0.0, group="Finished Goods", unit="Nos"):
    return {
        "Stock Item": name, "Stock Group": group, "Unit": unit,
        "Opening Qty": opening_qty, "Opening Value": opening_value,
        "Tally Closing Qty": closing_qty, "Tally Closing Value": closing_value,
    }


def _register_row(item, voucher_type, qty):
    return {"Stock Item": item, "Voucher Type": voucher_type, "Quantity": qty}


def test_build_stock_summary_ties_out():
    stock_items = [_stock_item("Widget", opening_qty=100, opening_value=10000, closing_qty=80, closing_value=8000)]
    register_rows = [
        _register_row("Widget", "Purchase", "50 Nos"),
        _register_row("Widget", "Sales", "70 Nos"),
    ]
    summary = build_stock_summary(stock_items, register_rows)
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["Opening Qty"] == 100.0
    assert row["Purchases Qty (period)"] == 50.0
    assert row["Sales Qty (period)"] == 70.0
    assert row["Derived Closing Qty"] == 80.0  # 100 + 50 - 70
    assert row["Tally Reported Closing Qty"] == 80.0
    assert row["Variance Qty"] == 0.0


def test_build_stock_summary_flags_variance():
    """A Stock Journal consumption not in the register -- expected to show
    up as variance, not silently absorbed."""
    stock_items = [_stock_item("Widget", opening_qty=100, closing_qty=60)]
    register_rows = [_register_row("Widget", "Sales", "30 Nos")]  # no purchases, no journal captured
    summary = build_stock_summary(stock_items, register_rows)
    row = summary.iloc[0]
    assert row["Derived Closing Qty"] == 70.0  # 100 - 30
    assert row["Variance Qty"] == -10.0  # Tally says 60, derived says 70


def test_build_stock_summary_excludes_returns():
    """Sales Return / Purchase Return / Credit Note / Debit Note lines are
    deliberately not netted -- see the module's documented limitation."""
    stock_items = [_stock_item("Widget", opening_qty=100, closing_qty=100)]
    register_rows = [
        _register_row("Widget", "Sales", "20 Nos"),
        _register_row("Widget", "Sales Return", "20 Nos"),  # ignored, not netted back
    ]
    summary = build_stock_summary(stock_items, register_rows)
    row = summary.iloc[0]
    assert row["Sales Qty (period)"] == 20.0  # Sales Return not subtracted
    assert row["Derived Closing Qty"] == 80.0


def test_build_stock_summary_no_register_activity():
    stock_items = [_stock_item("Widget", opening_qty=50, closing_qty=50)]
    summary = build_stock_summary(stock_items, [])
    row = summary.iloc[0]
    assert row["Derived Closing Qty"] == 50.0
    assert row["Variance Qty"] == 0.0


def test_build_stock_summary_empty_stock_items():
    assert build_stock_summary([], []).empty


def test_build_negative_stock_flags():
    stock_items = [
        _stock_item("Widget", opening_qty=10, closing_qty=-5),
        _stock_item("Gadget", opening_qty=10, closing_qty=5),
    ]
    summary = build_stock_summary(stock_items, [])
    flagged = build_negative_stock_flags(summary)
    assert len(flagged) == 1
    assert flagged.iloc[0]["Stock Item"] == "Widget"
