"""Phase 2: TDS deducted (books) vs deposited (challans) reconciliation.

tds_summary.build_tds_summary() gives a party-wise, month-wise, section-wise
view of what was *deducted* per the books. Combined_PF_Statutory.py's
extract_tds() parses ITNS-281 challans -- what was actually *deposited* with
the government -- but a challan only carries a Financial Year and an
aggregate deposit amount (no month, no section/nature-of-payment), so this
reconciliation can only operate at the **Financial Year total** level, not
party-wise or month-wise. That's a real limitation of the challan data
itself, not something this module works around by guessing -- it's stated
plainly in the output rather than fabricating a finer breakdown.
"""

from __future__ import annotations

import datetime
import re

import pandas as pd

RECON_COLUMNS = ["Financial Year", "TDS Deducted (Books)", "TDS Deposited (Challans)", "Difference", "Status"]


def _fy_from_month_label(label: str) -> str | None:
    """"Jan-2024" -> "2023-24" (Indian FY: April to March)."""
    try:
        dt = datetime.datetime.strptime(label, "%b-%Y")
    except (ValueError, TypeError):
        return None
    fy_start = dt.year if dt.month >= 4 else dt.year - 1
    return f"{fy_start}-{str(fy_start + 1)[2:]}"


def _normalize_fy(raw: str) -> str | None:
    """Challan "Financial Year" text varies in the wild ("2023-24",
    "2023-2024", "23-24") -- canonicalize to "YYYY-YY" so it joins against
    _fy_from_month_label()'s output. Returns None for anything unparseable
    rather than guessing."""
    if not raw:
        return None
    m = re.search(r"(\d{4})\s*-\s*(\d{2,4})", str(raw))
    if not m:
        return None
    start = int(m.group(1))
    return f"{start}-{str(start + 1)[2:]}"


def _to_float(val) -> float:
    try:
        return float(str(val).replace(",", "")) if val not in ("", None) else 0.0
    except ValueError:
        return 0.0


def build_tds_recon(tds_detail: pd.DataFrame, challan_rows: list[dict]) -> pd.DataFrame:
    """`tds_detail` is build_tds_summary()'s output (needs "Month" and
    "TDS Amount"); `challan_rows` is a list of extract_tds() dicts (needs
    "Financial Year" and "Amount (Rs)")."""
    deducted_by_fy: dict[str, float] = {}
    if tds_detail is not None and not tds_detail.empty:
        working = tds_detail.copy()
        working["_fy"] = working["Month"].map(_fy_from_month_label)
        for fy, group in working.groupby("_fy"):
            if fy is None:
                continue
            deducted_by_fy[fy] = deducted_by_fy.get(fy, 0.0) + float(group["TDS Amount"].sum())

    deposited_by_fy: dict[str, float] = {}
    for row in challan_rows or []:
        fy = _normalize_fy(row.get("Financial Year", ""))
        if fy is None:
            continue
        deposited_by_fy[fy] = deposited_by_fy.get(fy, 0.0) + _to_float(row.get("Amount (Rs)"))

    all_fys = sorted(set(deducted_by_fy) | set(deposited_by_fy))
    rows = []
    for fy in all_fys:
        deducted = deducted_by_fy.get(fy)
        deposited = deposited_by_fy.get(fy)
        if deducted is None:
            status = "Only in Challans (no TDS ledger activity found for this year)"
        elif deposited is None:
            status = "Only in Books (no challan uploaded for this year)"
        elif abs(deducted - deposited) <= 1.0:
            status = "Matched"
        elif deducted > deposited:
            status = "Under-deposited"
        else:
            status = "Over-deposited"
        rows.append({
            "Financial Year": fy,
            "TDS Deducted (Books)": deducted,
            "TDS Deposited (Challans)": deposited,
            "Difference": round((deducted or 0.0) - (deposited or 0.0), 2) if deducted is not None and deposited is not None else None,
            "Status": status,
        })
    return pd.DataFrame(rows, columns=RECON_COLUMNS)
