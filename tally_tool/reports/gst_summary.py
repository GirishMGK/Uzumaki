"""Month-wise GST Output/Input tax summary, built purely from Tally's own
books (ledger entries) -- no filed GSTR-1/GSTR-3B involved yet.

This is deliberately "as per books" only. Combined_PF_Statutory.py already
parses filed GSTR-1/GSTR-3B returns and reconciles them against each other
(extract_gstr1(), extract_gstr3b(), compute_recon()) -- wiring this books
summary into that as a third leg (a 3-way recon) is a follow-up phase, not
this one, since it needs the entity's GSTIN captured somewhere (Tally's
ledger master doesn't reliably carry it) and touches an already-shipped
reconciliation function other things may depend on.

INPUT SHAPE
-----------
`df` is the same row shape tally_connector.fetch_vouchers() and
extract_ledgers.extract()/extract_xml() already produce: one row per ledger
entry, with at least "Ledger Name", "Date", "Voucher Type", "Debit", "Credit".
`ledger_master` is the {name: {"group": ..., "opening_balance": ...}} dict
fetch_ledger_master()/extract_any() already build.

WHAT COUNTS AS "TAX AMOUNT"
----------------------------
For an Output (liability) ledger, a Credit entry is tax charged on sales/
services; a Debit entry reduces it (utilisation against ITC, or payment) --
so the period's Output tax is the *net* Credit-minus-Debit movement.
For an Input (ITC) ledger, a Debit entry is credit availed on purchases; a
Credit entry reduces it (utilised or reversed) -- so the period's Input tax
is the net Debit-minus-Credit movement. Ledgers this module can't confidently
place on either side keep their raw Debit/Credit instead of a signed "Tax
Amount" convention that would just be a guess -- see the Unclassified bucket.
"""

from __future__ import annotations

import pandas as pd

from .common import classify_gst_ledger, month_label, month_sort_key, resolve_ambiguous_direction

DETAIL_COLUMNS = ["Month", "Direction", "Tax Type", "Ledger Name", "Debit", "Credit", "Tax Amount", "Entry Count"]
PIVOT_TAX_TYPE_ORDER = ["CGST", "SGST", "IGST", "UTGST", "CESS"]


def build_gst_summary(df: pd.DataFrame, ledger_master: dict) -> pd.DataFrame:
    """Returns the ledger-level detail table: one row per
    Month x Direction x Tax Type x Ledger Name. Direction is "Output",
    "Input", or "Unclassified" (recognisably a GST tax ledger, but neither
    the ledger name nor its entries' voucher types could place it on a side
    -- e.g. a GST adjustment posted via a Journal voucher)."""
    if df is None or df.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)

    classification: dict[str, tuple[str | None, str | None]] = {}
    for name in df["Ledger Name"].unique():
        parent = ledger_master.get(name, {}).get("group", "")
        classification[name] = classify_gst_ledger(name, parent)

    tax_type_map = {n: c[1] for n, c in classification.items()}
    gst_df = df[df["Ledger Name"].map(tax_type_map).notna()].copy()
    if gst_df.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)

    gst_df["Tax Type"] = gst_df["Ledger Name"].map(tax_type_map)
    raw_direction = gst_df["Ledger Name"].map(lambda n: classification[n][0])

    def _resolve(direction, voucher_type):
        if direction in ("output", "input"):
            return direction
        return resolve_ambiguous_direction(voucher_type) or "unclassified"

    gst_df["Direction"] = [
        _resolve(d, vt) for d, vt in zip(raw_direction, gst_df.get("Voucher Type", ""))
    ]
    gst_df["Month"] = gst_df["Date"].map(month_label)

    summary = gst_df.groupby(["Month", "Direction", "Tax Type", "Ledger Name"], as_index=False).agg(
        Debit=("Debit", "sum"), Credit=("Credit", "sum"), **{"Entry Count": ("Debit", "size")}
    )
    summary["Tax Amount"] = summary.apply(
        lambda r: (r["Credit"] - r["Debit"]) if r["Direction"] == "output"
        else (r["Debit"] - r["Credit"]) if r["Direction"] == "input"
        else (r["Credit"] - r["Debit"]),
        axis=1,
    )
    summary["Direction"] = summary["Direction"].map(
        {"output": "Output", "input": "Input", "unclassified": "Unclassified"}
    )
    summary = summary.sort_values(
        by=["Month", "Direction", "Tax Type", "Ledger Name"],
        key=lambda col: col.map(month_sort_key) if col.name == "Month" else col,
    ).reset_index(drop=True)
    return summary[DETAIL_COLUMNS]


def build_month_pivot(detail_df: pd.DataFrame) -> pd.DataFrame:
    """Turns the ledger-level detail into the "Month-wise GSTR Summary"
    shape the user actually asked for: one row per month, one column per
    Direction x Tax Type combination, plus Total Output / Total Input / Net
    GST Payable. Only Output and Input rows feed this -- Unclassified rows
    are reported separately (build_unclassified() below) so they're never
    silently netted into a payable figure that looks authoritative."""
    columns = ["Month"] + [f"Output {t}" for t in PIVOT_TAX_TYPE_ORDER] + [f"Input {t}" for t in PIVOT_TAX_TYPE_ORDER] + [
        "Total Output", "Total Input", "Net GST Payable"
    ]
    if detail_df is None or detail_df.empty:
        return pd.DataFrame(columns=columns)

    working = detail_df[detail_df["Direction"].isin(["Output", "Input"])]
    if working.empty:
        return pd.DataFrame(columns=columns)

    pivot = working.pivot_table(
        index="Month", columns=["Direction", "Tax Type"], values="Tax Amount", aggfunc="sum", fill_value=0.0
    )
    pivot.columns = [f"{direction} {tax_type}" for direction, tax_type in pivot.columns]
    pivot = pivot.reset_index()

    for col in columns[1:-3]:
        if col not in pivot.columns:
            pivot[col] = 0.0

    output_cols = [f"Output {t}" for t in PIVOT_TAX_TYPE_ORDER]
    input_cols = [f"Input {t}" for t in PIVOT_TAX_TYPE_ORDER]
    pivot["Total Output"] = pivot[output_cols].sum(axis=1)
    pivot["Total Input"] = pivot[input_cols].sum(axis=1)
    pivot["Net GST Payable"] = pivot["Total Output"] - pivot["Total Input"]

    pivot = pivot.sort_values(by="Month", key=lambda s: s.map(month_sort_key)).reset_index(drop=True)
    return pivot[columns]


def build_unclassified(detail_df: pd.DataFrame) -> pd.DataFrame:
    """The ledgers/entries this module recognised as GST-related but
    couldn't confidently place as Output or Input -- surfaced separately so
    a reviewer can manually tag them rather than have them silently missing
    from the pivot above."""
    if detail_df is None or detail_df.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)
    return detail_df[detail_df["Direction"] == "Unclassified"].reset_index(drop=True)
