"""Phase 2: 3-way GST reconciliation -- "As per Books" (Tally, via
gst_summary.build_gst_summary/build_month_pivot) vs "As per GSTR-1" vs
"As per GSTR-3B" (both parsed from filed-return PDFs by
common/statutory_extractors.py's extract_gstr1()/extract_gstr3b(), the same
functions Combined_PF_Statutory.py already uses for its own GSTR-1-vs-GSTR-3B
reconciliation).

JOIN KEY -- A REAL LIMITATION INHERITED FROM compute_recon()
---------------------------------------------------------------
extract_gstr1()/extract_gstr3b() parse "Tax period" as a bare month name
(the source PDF's "Tax period" field has no year in it, and the filename-
based fallback carries the Financial Year separately rather than folding it
into the period text) -- so compute_recon() matches on (GSTIN, bare month
name) alone, e.g. "January", not "Jan-2024". This 3-way recon reuses
compute_recon() unchanged (rather than re-deriving that matching a second,
possibly-inconsistent way), so it has to key its own books-side month
totals the same way: by bare month name, collapsing Tally's "%b-%Y" labels
down to just the month. That means if you pull more than one financial
year of Tally data into a single reconciliation, entries from different
years with the same month name are summed together rather than kept apart
-- flagged via `collisions` below, exactly the same "duplicate key" signal
compute_recon() itself raises for a duplicate GSTR-3B upload. Run this one
FY at a time for a clean match, same as the existing GSTR-1/GSTR-3B tool
already expects.

Tally's ledger master doesn't reliably carry the entity's GSTIN either, so
the books side has no GSTIN of its own -- the caller (the Streamlit page)
resolves one via resolve_gstin() (an explicit entry, or auto-detected from
the uploaded returns) and passes it in.
"""

from __future__ import annotations

import datetime
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common.period_utils import _MONTH_NUM  # noqa: E402
from common.statutory_extractors import compute_recon  # noqa: E402

RECON_COLUMNS = [
    "GSTIN", "Month", "Books Output Tax", "Books Input Tax", "Books Net Payable",
    "GSTR-1 Tax (Output)", "GSTR-3B Tax", "Diff: Books vs GSTR-1", "Diff: Books vs GSTR-3B",
    "GSTR-1 vs GSTR-3B Status", "Status",
]


def _bare_month_name(label: str) -> str | None:
    """"Jan-2024" -> "January" -- matches the bare month name
    compute_recon()'s key uses (see module docstring)."""
    try:
        dt = datetime.datetime.strptime(label, "%b-%Y")
    except (ValueError, TypeError):
        return None
    return _MONTH_NUM[dt.month]


def _books_month_totals(month_pivot: pd.DataFrame) -> tuple[dict[str, dict], set[str]]:
    """Returns ({bare_month_name: {"output":.., "input":..}}, collisions) --
    collisions is the set of month names that summed data from more than
    one calendar year in this pivot (see module docstring)."""
    totals: dict[str, dict] = {}
    seen_years: dict[str, set] = {}
    collisions: set[str] = set()
    for _, row in month_pivot.iterrows():
        month = _bare_month_name(row["Month"])
        if month is None:
            continue
        year = str(row["Month"])[-4:]
        seen_years.setdefault(month, set()).add(year)
        if len(seen_years[month]) > 1:
            collisions.add(month)
        bucket = totals.setdefault(month, {"output": 0.0, "input": 0.0})
        bucket["output"] += float(row.get("Total Output", 0.0))
        bucket["input"] += float(row.get("Total Input", 0.0))
    return totals, collisions


def resolve_gstin(gstin: str, gstr1_rows: list[dict], gstr3b_rows: list[dict]) -> tuple[str, str]:
    """Returns (resolved_gstin, note). If the user entered a GSTIN, that
    wins. Otherwise, if the uploaded filed-return files all share exactly
    one non-blank GSTIN (the common case -- one entity's own returns
    uploaded together), that's used automatically. With more than one
    distinct GSTIN and no explicit entry, resolution fails (empty string)
    rather than guessing which one the books belong to."""
    gstin = (gstin or "").strip().upper()
    if gstin:
        return gstin, ""
    seen = {str(r.get("GSTIN", "")).strip().upper() for r in (gstr1_rows + gstr3b_rows)}
    seen.discard("")
    if len(seen) == 1:
        only = next(iter(seen))
        return only, f"GSTIN not entered — auto-detected {only} from the uploaded filed returns."
    if len(seen) > 1:
        return "", "Multiple GSTINs found across the uploaded filed returns — enter the entity's GSTIN to match the books to the right one."
    return "", "No GSTIN entered and none found in the uploaded filed returns."


def compute_recon_3way(
    month_pivot: pd.DataFrame, gstin: str, gstr1_rows: list[dict], gstr3b_rows: list[dict]
) -> tuple[pd.DataFrame, set[str]]:
    """Returns (result_df, collisions) -- one row per month appearing in the
    books, GSTR-1, or GSTR-3B side for the resolved GSTIN, never dropping a
    month just because one leg is missing it (same "Only in X" transparency
    compute_recon() already follows for the two-way case). `collisions` is
    the set of months where the books pivot spanned more than one calendar
    year -- surface it to the user rather than silently netting them.

    `gstin` is applied to every books-side month (Tally's own ledger master
    doesn't carry it reliably) -- pass the entity's GSTIN as entered by the
    user, or resolved via resolve_gstin().
    """
    gstin = (gstin or "").strip().upper()
    two_way, _dup_keys = compute_recon(gstr1_rows, gstr3b_rows)
    two_way_by_key = {(r["GSTIN"], r["Tax Period"]): r for r in two_way}

    books_totals, collisions = (
        _books_month_totals(month_pivot) if month_pivot is not None and not month_pivot.empty else ({}, set())
    )

    all_months = set(books_totals.keys()) | {k[1] for k in two_way_by_key if k[0] == gstin}

    month_order = list(_MONTH_NUM.values())
    rows = []
    for month in sorted(all_months, key=lambda m: month_order.index(m) if m in month_order else 99):
        books = books_totals.get(month)
        two = two_way_by_key.get((gstin, month))

        books_output = books["output"] if books else None
        books_input = books["input"] if books else None
        books_net = (books_output - books_input) if books else None

        gstr1_tax = two["GSTR-1 Tax"] if two else None
        gstr3b_tax = two["GSTR-3B Tax"] if two else None
        two_status = two["Status"] if two else ("Not filed / not uploaded" if books else "")

        diff_g1 = round(books_output - gstr1_tax, 2) if (books_output is not None and gstr1_tax is not None) else None
        diff_g3b = round(books_net - gstr3b_tax, 2) if (books_net is not None and gstr3b_tax is not None) else None

        if books is None:
            status = "Only in filed return"
        elif two is None:
            status = "Only in Books (no filed return found for this month)"
        elif diff_g1 is not None and abs(diff_g1) <= 1.0 and diff_g3b is not None and abs(diff_g3b) <= 1.0:
            status = "Matched"
        else:
            status = "Books vs Return Mismatch"

        rows.append({
            "GSTIN": gstin, "Month": month,
            "Books Output Tax": books_output, "Books Input Tax": books_input, "Books Net Payable": books_net,
            "GSTR-1 Tax (Output)": gstr1_tax, "GSTR-3B Tax": gstr3b_tax,
            "Diff: Books vs GSTR-1": diff_g1, "Diff: Books vs GSTR-3B": diff_g3b,
            "GSTR-1 vs GSTR-3B Status": two_status, "Status": status,
        })

    return pd.DataFrame(rows, columns=RECON_COLUMNS), collisions
