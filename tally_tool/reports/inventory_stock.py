"""Inventory closing stock -- item-wise quantity and value, as of a date.

Tally's StockItem master (tally_connector.fetch_stock_items()) carries
OPENINGBALANCE/OPENINGVALUE (reliable -- the opening figure for the current
book period) and CLOSINGBALANCE/CLOSINGVALUE, but the closing figures are
Tally's *current-period* numbers, not necessarily "as of" whatever date the
caller actually wants. To get a true as-of-date closing figure, this module
derives one instead:

    Derived Closing Qty = Opening Qty + Purchases Qty (up to the date)
                                       - Sales Qty (up to the date)

using the Sales/Purchase Register's item-wise quantities (reports/
sales_purchase_register.py / tally_connector.fetch_voucher_register()) as
the movement source, and compares it against Tally's own reported closing
as a should-tie check -- same control-total spirit as extract_ledgers.py's
Debit-vs-Credit check, except a persistent variance here is *expected*
wherever a company uses Stock Journal/Manufacturing Journal vouchers,
because those movements aren't in the Sales/Purchase register at all. The
variance column is the actual audit-relevant signal (it flags un-invoiced
stock movement worth asking about), not a bug to chase to zero.

KNOWN LIMITATIONS (documented, not silently papered over)
-----------------------------------------------------------
- Only plain "Sales" and "Purchase" voucher-type lines are netted into the
  derived closing figure. Sales Return / Purchase Return / Credit Note /
  Debit Note lines are excluded -- their quantity sign convention in
  Tally's own export (does a Sales Return come back qty-negative already,
  or does the caller need to flip it?) isn't verified against a real Tally
  instance, and guessing wrong would corrupt the derived figure silently.
  This means expect *additional* variance for companies with material
  returns, on top of the Stock Journal gap above.
- Stock Journal / Manufacturing Journal voucher types aren't fetched at all
  in this pass (a Tier-2 extension, not built here).
"""

from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import extract_ledgers  # noqa: E402  (tally_tool/extract_ledgers.py, path set up above)

from .common import parse_qty

STOCK_SUMMARY_COLUMNS = [
    "Stock Item", "Stock Group", "Unit",
    "Opening Qty", "Opening Value",
    "Purchases Qty (period)", "Sales Qty (period)",
    "Derived Closing Qty",
    "Tally Reported Closing Qty", "Tally Reported Closing Value",
    "Variance Qty",
]

# Deliberately narrower than reports/common.py's GST-direction voucher-type
# sets -- see the "Known limitations" note above for why Returns/Notes are
# excluded here specifically, even though they're netted for GST purposes.
_SALES_TYPES = {"sales"}
_PURCHASE_TYPES = {"purchase"}


def build_stock_summary(stock_items: list[dict], register_rows: list[dict]) -> pd.DataFrame:
    """`stock_items` is fetch_stock_items()'s output. `register_rows` is
    Sales+Purchase register rows (fetch_voucher_register()/
    extract_register_from_export() output for BOTH voucher types, combined
    by the caller) already filtered to whatever date range the caller wants
    "purchases/sales in the period" to mean -- this function does no date
    filtering of its own, it just aggregates what it's given."""
    if not stock_items:
        return pd.DataFrame(columns=STOCK_SUMMARY_COLUMNS)

    reg_df = pd.DataFrame(register_rows) if register_rows else pd.DataFrame(columns=["Stock Item", "Voucher Type", "Quantity"])
    if not reg_df.empty and "Voucher Type" in reg_df.columns:
        reg_df["_vtype"] = reg_df["Voucher Type"].str.strip().str.lower()
        reg_df["_qty"] = reg_df["Quantity"].map(parse_qty)
        purchases = reg_df[reg_df["_vtype"].isin(_PURCHASE_TYPES)].groupby("Stock Item")["_qty"].sum()
        sales = reg_df[reg_df["_vtype"].isin(_SALES_TYPES)].groupby("Stock Item")["_qty"].sum()
    else:
        purchases = pd.Series(dtype=float)
        sales = pd.Series(dtype=float)

    rows = []
    for item in stock_items:
        name = item.get("Stock Item", "")
        opening_qty = float(item.get("Opening Qty", 0.0))
        purchase_qty = float(purchases.get(name, 0.0))
        sale_qty = float(sales.get(name, 0.0))
        derived_closing = opening_qty + purchase_qty - sale_qty
        tally_closing = float(item.get("Tally Closing Qty", 0.0))
        rows.append({
            "Stock Item": name,
            "Stock Group": item.get("Stock Group", ""),
            "Unit": item.get("Unit", ""),
            "Opening Qty": opening_qty,
            "Opening Value": float(item.get("Opening Value", 0.0)),
            "Purchases Qty (period)": purchase_qty,
            "Sales Qty (period)": sale_qty,
            "Derived Closing Qty": derived_closing,
            "Tally Reported Closing Qty": tally_closing,
            "Tally Reported Closing Value": float(item.get("Tally Closing Value", 0.0)),
            "Variance Qty": round(tally_closing - derived_closing, 4),
        })
    return pd.DataFrame(rows, columns=STOCK_SUMMARY_COLUMNS)


def extract_stock_items_from_export(utf8_path: str) -> list[dict]:
    """Best-effort file-based path: scans the same JSON/XML Data Interchange
    export the other pages read for Stock Item masters (mtype/tag "Stock
    Item"/"StockItem"/"STOCKITEM" -- Tally's exact label for this master
    type in that export format isn't confirmed against a real instance, so
    all the plausible spellings are checked). Returns an empty list -- not
    an error -- if none are found, since it's genuinely unclear without a
    real export whether "Export All" from the Day Book includes Stock Item
    masters at all; the caller should treat an empty result as "try the live
    connection instead" rather than "this export is broken".
    """
    fmt = extract_ledgers.sniff_format(utf8_path)
    if fmt == "xml":
        return _extract_stock_items_xml(utf8_path)
    return _extract_stock_items_json(utf8_path)


def _extract_stock_items_json(utf8_path: str) -> list[dict]:
    import ijson

    items: list[dict] = []
    with open(utf8_path, "rb") as f:
        for entry in ijson.items(f, "tallymessage.item"):
            meta = entry.get("metadata") or {}
            if meta.get("type") not in ("Stock Item", "StockItem"):
                continue
            name = extract_ledgers.clean_str(meta.get("name"))
            if not name:
                continue
            items.append({
                "Stock Item": name,
                "Stock Group": extract_ledgers.clean_str(entry.get("parent")),
                "Unit": extract_ledgers.clean_str(entry.get("baseunits")),
                "Opening Qty": parse_qty(entry.get("openingbalance")),
                "Opening Value": extract_ledgers.clean_num(entry.get("openingvalue")),
                "Tally Closing Qty": parse_qty(entry.get("closingbalance")),
                "Tally Closing Value": extract_ledgers.clean_num(entry.get("closingvalue")),
            })
    return items


def _extract_stock_items_xml(utf8_path: str) -> list[dict]:
    import xml.etree.ElementTree as ET

    def _text(el, tag, default=""):
        child = el.find(tag)
        if child is None or child.text is None:
            return default
        return child.text.strip()

    items: list[dict] = []
    for _event, elem in ET.iterparse(utf8_path, events=("end",)):
        if elem.tag not in ("STOCKITEM",):
            continue
        name = extract_ledgers.clean_str(elem.get("NAME") or _text(elem, "NAME"))
        if name:
            items.append({
                "Stock Item": name,
                "Stock Group": extract_ledgers.clean_str(_text(elem, "PARENT")),
                "Unit": extract_ledgers.clean_str(_text(elem, "BASEUNITS")),
                "Opening Qty": parse_qty(_text(elem, "OPENINGBALANCE")),
                "Opening Value": extract_ledgers.clean_num(_text(elem, "OPENINGVALUE")),
                "Tally Closing Qty": parse_qty(_text(elem, "CLOSINGBALANCE")),
                "Tally Closing Value": extract_ledgers.clean_num(_text(elem, "CLOSINGVALUE")),
            })
        elem.clear()
    return items


def build_negative_stock_flags(summary: pd.DataFrame) -> pd.DataFrame:
    """Items with a negative Derived or Tally-reported closing quantity --
    a classic forensic red flag (physically impossible without a data/
    process problem: unrecorded purchases, a fabricated sale, or a costing
    error). Returned separately so a page can surface it prominently rather
    than have it get lost in a large item list."""
    if summary is None or summary.empty:
        return summary
    return summary[(summary["Derived Closing Qty"] < 0) | (summary["Tally Reported Closing Qty"] < 0)]
