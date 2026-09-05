"""Party-wise TDS-deducted summary, built purely from Tally's own books --
this is the "what did we deduct from vendors" (payable) side, not the
"what did customers deduct from us" (receivable) side form26as_tool already
handles. No recon against deposited challans yet (Combined_PF_Statutory.py's
extract_tds() parses those) -- that's a follow-up phase, same reasoning as
gst_summary.py's books-only scope this pass.

INPUT SHAPE
-----------
`df` is the same row shape tally_connector.fetch_vouchers() and
extract_ledgers.extract()/extract_xml() already produce -- one row per
ledger entry, with "Ledger Name", "Date", "Voucher Type", "Voucher No",
"Party Ledger", "Debit", "Credit", and (for voucher grouping) "Voucher GUID"/
"Master ID". `ledger_master` is the {name: {"group": ...}} dict already
built by fetch_ledger_master()/extract_any().

PARTY TRACING
-------------
"Party Ledger" is already captured at voucher level on every row -- no new
fetch needed. Where it's blank (some Payment/Journal vouchers don't set it),
this falls back to the largest-magnitude non-TDS, non-cash/bank ledger entry
in the *same voucher* as the inferred deductee, and flags that row so a
reviewer can verify rather than trust it silently.
"""

from __future__ import annotations

import pandas as pd

from .common import classify_tds_ledger, month_label, month_sort_key

DETAIL_COLUMNS = [
    "Party (Deductee)", "PAN", "Nature of Payment", "Month", "TDS Amount",
    "Entry Count", "Party Inferred", "Source Voucher Numbers",
]

_CASH_BANK_KEYWORDS = ("cash", "bank")


def _voucher_key(row) -> str:
    guid = str(row.get("Voucher GUID") or "").strip()
    if guid:
        return f"guid:{guid}"
    master_id = str(row.get("Master ID") or "").strip()
    if master_id:
        return f"mid:{master_id}"
    # Fallback for rows/fixtures without either id -- voucher no + date +
    # type is not guaranteed unique across a whole company, but is the best
    # available grouping key when Tally hasn't supplied GUID/Master ID.
    return f"vno:{row.get('Voucher No')}|{row.get('Date')}|{row.get('Voucher Type')}"


def _is_cash_or_bank(name: str, ledger_master: dict) -> bool:
    group = ledger_master.get(name, {}).get("group", "").lower()
    return any(kw in group for kw in _CASH_BANK_KEYWORDS)


def _infer_party(voucher_rows: pd.DataFrame, tds_ledger_names: set, ledger_master: dict) -> str:
    candidates = voucher_rows[~voucher_rows["Ledger Name"].isin(tds_ledger_names)]
    candidates = candidates[~candidates["Ledger Name"].map(lambda n: _is_cash_or_bank(n, ledger_master))]
    if candidates.empty:
        return ""
    magnitude = (candidates["Debit"] - candidates["Credit"]).abs()
    return candidates.loc[magnitude.idxmax(), "Ledger Name"]


def build_tds_summary(df: pd.DataFrame, ledger_master: dict) -> pd.DataFrame:
    """Returns one row per Party (Deductee) x Nature of Payment x Month."""
    if df is None or df.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)

    classification = {}
    for name in df["Ledger Name"].unique():
        parent = ledger_master.get(name, {}).get("group", "")
        classification[name] = classify_tds_ledger(name, parent)

    tds_ledger_names = {n for n, (is_tds, _nature) in classification.items() if is_tds}
    if not tds_ledger_names:
        return pd.DataFrame(columns=DETAIL_COLUMNS)

    working = df.copy()
    working["_voucher_key"] = working.apply(_voucher_key, axis=1)
    voucher_groups = {key: rows for key, rows in working.groupby("_voucher_key")}

    tds_rows = working[working["Ledger Name"].isin(tds_ledger_names)].copy()
    tds_rows["Nature of Payment"] = tds_rows["Ledger Name"].map(
        lambda n: classification[n][1] or "Unclassified"
    )

    parties, inferred_flags = [], []
    for _, row in tds_rows.iterrows():
        party = str(row.get("Party Ledger") or "").strip()
        inferred = not party
        if inferred:
            voucher_rows = voucher_groups.get(row["_voucher_key"])
            party = _infer_party(voucher_rows, tds_ledger_names, ledger_master) if voucher_rows is not None else ""
        parties.append(party or "(Party not determinable)")
        inferred_flags.append(inferred)
    tds_rows["Party (Deductee)"] = parties
    tds_rows["Party Inferred"] = inferred_flags
    tds_rows["Month"] = tds_rows["Date"].map(month_label)

    group_keys = ["Party (Deductee)", "Nature of Payment", "Month"]
    detail = tds_rows.groupby(group_keys, as_index=False).agg(
        Credit=("Credit", "sum"),
        Debit=("Debit", "sum"),
        **{"Entry Count": ("Debit", "size")},
        **{"Party Inferred": ("Party Inferred", "any")},
    )
    # TDS is a liability ledger -- deduction posts as Credit, so the net
    # deducted-during-the-period figure is Credit minus Debit (a Debit here
    # is a reversal/correction, or the eventual payment to the government).
    detail["TDS Amount"] = detail["Credit"] - detail["Debit"]

    voucher_lists = (
        tds_rows.groupby(group_keys)["Voucher No"]
        .apply(lambda s: "; ".join(sorted({str(v).strip() for v in s if str(v).strip()})))
        .reset_index()
        .rename(columns={"Voucher No": "Source Voucher Numbers"})
    )
    detail = detail.merge(voucher_lists, on=group_keys, how="left")

    # PAN isn't in the current Tally FETCH field set (INCOMETAXNUMBER on the
    # ledger master would need adding -- deferred, see the plan's roadmap
    # section) -- included as a blank column rather than fabricated, so a
    # future recon phase has a stable column to fill in.
    detail["PAN"] = ""

    detail = detail.sort_values(
        by=["Month", "Party (Deductee)", "Nature of Payment"],
        key=lambda col: col.map(month_sort_key) if col.name == "Month" else col,
    ).reset_index(drop=True)

    return detail[DETAIL_COLUMNS]
