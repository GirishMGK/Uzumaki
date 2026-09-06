"""Sales/Purchase Register -- file-based export path, plus per-voucher GST
breakup and an HSN-wise summary built on top of it.

tally_connector.fetch_voucher_register() already pulls this live (item-wise:
Stock Item, Quantity, Rate, Item Amount, plus each voucher's overall value,
and -- since Phase 3 -- an optional CGST/SGST/IGST/UTGST/CESS breakup and
HSN Code). extract_ledgers.py's JSON/XML export parsing already streams
every Voucher looking for ledger entries (allledgerentries/
ALLLEDGERENTRIES.LIST) but never reads the sibling inventory entries
(allinventoryentries/ALLINVENTORYENTRIES.LIST) that are sitting in the very
same export -- this module adds that second pass so a Sales/Purchase
Register doesn't require a live Tally connection at all.

extract_register_from_export() returns rows in EXACTLY the shape
fetch_voucher_register() already returns, so the hub page can render/export
either source with one shared function -- same "shared shape, two producers"
pattern extract_ledgers.extract_any()/tally_connector.pull_from_tally()
already use for the ledger-wise extraction.
"""

from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import extract_ledgers  # noqa: E402  (tally_tool/extract_ledgers.py, path set up above)

from .common import classify_gst_ledger, parse_qty

REGISTER_COLUMNS = [
    "Date", "Voucher Type", "Voucher No", "Party Ledger", "Reference", "Narration",
    "Stock Item", "HSN Code", "Quantity", "Rate", "Item Amount", "Voucher Total",
    "CGST", "SGST", "IGST", "UTGST", "CESS",
    "Voucher GUID", "Master ID",
]
_GST_TAX_TYPES = ("CGST", "SGST", "IGST", "UTGST", "CESS")


def _voucher_total(ledger_entries: list[dict], party: str, amount_key: str) -> float:
    """Same logic as tally_connector.fetch_voucher_register(): the invoice's
    overall (GST-inclusive) value is the sum of the ledger entries that
    AREN'T the party ledger -- falls back to any entry's amount if none
    matched (e.g. a party-name mismatch)."""
    total = sum(
        abs(extract_ledgers.clean_num(e.get(amount_key)))
        for e in ledger_entries
        if extract_ledgers.clean_str(e.get("ledgername")) != party
    )
    if total == 0.0 and ledger_entries:
        total = abs(extract_ledgers.clean_num(ledger_entries[0].get(amount_key)))
    return total


def _gst_breakup(ledger_entries: list[dict], ledger_master: dict | None) -> dict[str, float]:
    """Mirrors tally_connector._gst_breakup_from_entries() for the file-based
    path -- kept as a separate small copy rather than a cross-module import
    since tally_connector.py is transport code and shouldn't need to import
    this sibling module just to share four lines; both call the same
    classify_gst_ledger() heuristic underneath."""
    breakup = {t: 0.0 for t in _GST_TAX_TYPES}
    if not ledger_master:
        return breakup
    for e in ledger_entries:
        lname = extract_ledgers.clean_str(e.get("ledgername"))
        if not lname:
            continue
        _direction, tax_type = classify_gst_ledger(lname, ledger_master.get(lname, {}).get("group", ""))
        if tax_type:
            breakup[tax_type] += abs(extract_ledgers.clean_num(e.get("amount")))
    return breakup


def _extract_register_json(
    utf8_path: str, voucher_types: set, include_cancelled: bool, ledger_master: dict | None
) -> list[dict]:
    import ijson

    rows: list[dict] = []
    with open(utf8_path, "rb") as f:
        for item in ijson.items(f, "tallymessage.item"):
            meta = item.get("metadata") or {}
            if meta.get("type") != "Voucher":
                continue
            vch_type = extract_ledgers.clean_str(item.get("vouchertypename") or meta.get("vchtype"))
            if voucher_types and vch_type not in voucher_types:
                continue
            is_cancelled = bool(item.get("iscancelled"))
            is_optional = bool(item.get("isoptional"))
            if not include_cancelled and (is_cancelled or is_optional):
                continue

            date = extract_ledgers.parse_tally_date(item.get("date"))
            vch_no = extract_ledgers.clean_str(item.get("vouchernumber"))
            party = extract_ledgers.clean_str(item.get("partyledgername"))
            reference = extract_ledgers.clean_str(item.get("reference"))
            narration = extract_ledgers.clean_str(item.get("narration"))
            guid = extract_ledgers.clean_str((meta.get("remoteid")) or item.get("guid"))
            master_id = extract_ledgers.clean_str(item.get("masterid"))

            ledger_entries = list(item.get("allledgerentries") or []) + list(item.get("ledgerentries") or [])
            voucher_total = _voucher_total(ledger_entries, party, "amount")
            gst = _gst_breakup(ledger_entries, ledger_master)

            item_entries = item.get("allinventoryentries") or []
            if item_entries:
                for inv in item_entries:
                    qty = inv.get("actualqty") or inv.get("billedqty")
                    rows.append({
                        "Date": date, "Voucher Type": vch_type, "Voucher No": vch_no,
                        "Party Ledger": party, "Reference": reference, "Narration": narration,
                        "Stock Item": extract_ledgers.clean_str(inv.get("stockitemname")),
                        "HSN Code": extract_ledgers.clean_str(inv.get("gsthsnname") or inv.get("hsncode")),
                        "Quantity": extract_ledgers.clean_str(qty),
                        "Rate": extract_ledgers.clean_str(inv.get("rate")),
                        "Item Amount": abs(extract_ledgers.clean_num(inv.get("amount"))),
                        "Voucher Total": voucher_total,
                        **gst,
                        "Voucher GUID": guid, "Master ID": master_id,
                    })
            else:
                # Service invoice or similar with no stock items -- still a
                # row, without item-level detail, same as the live version.
                rows.append({
                    "Date": date, "Voucher Type": vch_type, "Voucher No": vch_no,
                    "Party Ledger": party, "Reference": reference, "Narration": narration,
                    "Stock Item": "", "HSN Code": "", "Quantity": "", "Rate": "",
                    "Item Amount": voucher_total, "Voucher Total": voucher_total,
                    **gst,
                    "Voucher GUID": guid, "Master ID": master_id,
                })
    return rows


def _extract_register_xml(
    utf8_path: str, voucher_types: set, include_cancelled: bool, ledger_master: dict | None
) -> list[dict]:
    import xml.etree.ElementTree as ET

    def _text(el, tag, default=""):
        child = el.find(tag)
        if child is None or child.text is None:
            return default
        return child.text.strip()

    def _yes(el, tag):
        return _text(el, tag).strip().lower() in ("yes", "true", "1")

    rows: list[dict] = []
    for _event, elem in ET.iterparse(utf8_path, events=("end",)):
        if elem.tag != "VOUCHER":
            continue
        vch_type = extract_ledgers.clean_str(_text(elem, "VOUCHERTYPENAME") or elem.get("VCHTYPE"))
        if voucher_types and vch_type not in voucher_types:
            elem.clear()
            continue
        is_cancelled = _yes(elem, "ISCANCELLED")
        is_optional = _yes(elem, "ISOPTIONAL")
        if not include_cancelled and (is_cancelled or is_optional):
            elem.clear()
            continue

        date = extract_ledgers.parse_tally_date(_text(elem, "DATE"))
        vch_no = extract_ledgers.clean_str(_text(elem, "VOUCHERNUMBER"))
        party = extract_ledgers.clean_str(_text(elem, "PARTYLEDGERNAME"))
        reference = extract_ledgers.clean_str(_text(elem, "REFERENCE"))
        narration = extract_ledgers.clean_str(_text(elem, "NARRATION"))
        guid = extract_ledgers.clean_str(_text(elem, "GUID") or _text(elem, "REMOTEID"))
        master_id = extract_ledgers.clean_str(_text(elem, "MASTERID"))

        ledger_entry_els = elem.findall("ALLLEDGERENTRIES.LIST") + elem.findall("LEDGERENTRIES.LIST")
        ledger_entries = [{"ledgername": _text(e, "LEDGERNAME"), "amount": _text(e, "AMOUNT")} for e in ledger_entry_els]
        voucher_total = _voucher_total(ledger_entries, party, "amount")
        gst = _gst_breakup(ledger_entries, ledger_master)

        item_entries = elem.findall("ALLINVENTORYENTRIES.LIST")
        if item_entries:
            for inv in item_entries:
                qty = _text(inv, "ACTUALQTY") or _text(inv, "BILLEDQTY")
                rows.append({
                    "Date": date, "Voucher Type": vch_type, "Voucher No": vch_no,
                    "Party Ledger": party, "Reference": reference, "Narration": narration,
                    "Stock Item": extract_ledgers.clean_str(_text(inv, "STOCKITEMNAME")),
                    "HSN Code": extract_ledgers.clean_str(_text(inv, "GSTHSNNAME") or _text(inv, "HSNCODE")),
                    "Quantity": extract_ledgers.clean_str(qty),
                    "Rate": extract_ledgers.clean_str(_text(inv, "RATE")),
                    "Item Amount": abs(extract_ledgers.clean_num(_text(inv, "AMOUNT"))),
                    "Voucher Total": voucher_total,
                    **gst,
                    "Voucher GUID": guid, "Master ID": master_id,
                })
        else:
            rows.append({
                "Date": date, "Voucher Type": vch_type, "Voucher No": vch_no,
                "Party Ledger": party, "Reference": reference, "Narration": narration,
                "Stock Item": "", "HSN Code": "", "Quantity": "", "Rate": "",
                "Item Amount": voucher_total, "Voucher Total": voucher_total,
                **gst,
                "Voucher GUID": guid, "Master ID": master_id,
            })
        elem.clear()
    return rows


def extract_register_from_export(
    utf8_path: str, voucher_types: set, include_cancelled: bool = False, ledger_master: dict | None = None
) -> list[dict]:
    """voucher_types: exact Tally voucher type names to include (e.g.
    {"Sales"} or {"Purchase"}), matched case-sensitively against
    VOUCHERTYPENAME/vouchertypename exactly as fetch_voucher_register()
    already does for the live path -- pass an empty set to include every
    voucher type in the export.

    `ledger_master` is the {name: {"group": ...}} dict extract_any() already
    returns from the very same export -- pass it (no extra file read needed)
    to also get a per-voucher CGST/SGST/IGST/UTGST/CESS breakup on every row.
    Omit it to skip the breakup -- columns come back all-zero rather than
    the function failing."""
    fmt = extract_ledgers.sniff_format(utf8_path)
    if fmt == "xml":
        return _extract_register_xml(utf8_path, voucher_types, include_cancelled, ledger_master)
    return _extract_register_json(utf8_path, voucher_types, include_cancelled, ledger_master)


def build_hsn_summary(df: pd.DataFrame) -> pd.DataFrame:
    """HSN-wise summary (Table 12 of GSTR-1's shape: HSN Code x quantity x
    value) from a register DataFrame -- one row per HSN Code x Voucher Type,
    summing Quantity (parsed via parse_qty(), since the register's Quantity
    column carries a unit suffix like "10 Nos") and Item Amount."""
    columns = ["HSN Code", "Voucher Type", "Quantity", "Item Amount", "Line Count"]
    if df is None or df.empty or "HSN Code" not in df.columns:
        return pd.DataFrame(columns=columns)

    working = df[df["HSN Code"].astype(str).str.strip() != ""].copy()
    if working.empty:
        return pd.DataFrame(columns=columns)

    working["_qty"] = working["Quantity"].map(parse_qty)
    summary = working.groupby(["HSN Code", "Voucher Type"], as_index=False).agg(
        Quantity=("_qty", "sum"),
        **{"Item Amount": ("Item Amount", "sum"), "Line Count": ("Item Amount", "size")},
    )
    return summary[columns]
