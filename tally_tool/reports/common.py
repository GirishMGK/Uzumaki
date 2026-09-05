"""Shared helpers for the Tally report modules: GST/TDS ledger classification,
cost-centre presence check, and the month-label used to pivot transactions.

CLASSIFICATION IS HEURISTIC, NOT AUTHORITATIVE
------------------------------------------------
Tally's own GST classification metadata (GSTDETAILS.LIST on a ledger) is
inconsistently populated across versions/releases -- some companies never
touch it even though the ledger is genuinely used for GST, others have it
set but stale after a rename. Rather than trust that field, classify_gst_
ledger()/classify_tds_ledger() work off the ledger's Group (PARENT) plus
name-pattern keywords, the same way a reviewing accountant would recognize
"Output CGST" or "TDS on Contractors" by eye. Anything that doesn't match
comes back unclassified rather than being silently dropped or guessed wrong
-- every report built on these functions renders its "Unclassified"/
"ambiguous" bucket as its own visible section.
"""

from __future__ import annotations

import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common.period_utils import month_label  # noqa: E402  (re-exported for report modules)

__all__ = [
    "month_label",
    "month_sort_key",
    "has_cost_centre_data",
    "classify_gst_ledger",
    "resolve_ambiguous_direction",
    "classify_tds_ledger",
]


def month_sort_key(label: str) -> datetime.date:
    """Sort key for a "%b-%Y" month_label() string -- chronological, not
    alphabetical (so "Feb-2024" doesn't sort before "Jan-2025")."""
    try:
        return datetime.datetime.strptime(label, "%b-%Y").date()
    except (ValueError, TypeError):
        return datetime.date.min


def has_cost_centre_data(df) -> bool:
    """True if any row actually carries a Cost Centre value -- lets a page
    skip offering a cost-centre pivot/extra sheet when the column would be
    entirely blank (cost centres not enabled for this company), same
    graceful-degrade principle as the column itself already follows."""
    if df is None or "Cost Centre" not in df.columns or df.empty:
        return False
    return bool((df["Cost Centre"].astype(str).str.strip() != "").any())


# --------------------------------------------------------------------------
# GST ledger classification
# --------------------------------------------------------------------------
_TAX_TYPE_PATTERNS = [
    ("IGST", re.compile(r"\bIGST\b")),
    ("UTGST", re.compile(r"\bUTGST\b")),
    ("CGST", re.compile(r"\bCGST\b")),
    ("SGST", re.compile(r"\bSGST\b")),
    ("CESS", re.compile(r"\bCESS\b")),
]
_GST_OUTPUT_KEYWORDS = re.compile(r"\bOUTPUT\b|\bPAYABLE\b|\bLIABILIT(Y|IES)\b|\bCOLLECTED\b", re.I)
_GST_INPUT_KEYWORDS = re.compile(r"\bINPUT\b|\bITC\b|\bCREDIT\b|\bRECEIVABLE\b", re.I)

# Voucher types used to resolve a GST ledger entry's direction when the
# ledger's own name carries no Output/Input keyword (common in smaller
# companies' books, e.g. a single bare "CGST" ledger used for both legs).
_OUTPUT_VOUCHER_TYPES = {"sales", "sales return", "credit note", "export sales"}
_INPUT_VOUCHER_TYPES = {"purchase", "purchase return", "debit note", "import purchase"}


def classify_gst_ledger(name: str, parent: str = "") -> tuple[str | None, str | None]:
    """Returns (direction, tax_type).

    direction is one of:
      "output"    -- ledger name itself says so (Output/Payable/Liability/Collected)
      "input"     -- ledger name itself says so (Input/ITC/Credit/Receivable)
      "ambiguous" -- recognisably a GST tax ledger (matched a tax-type keyword)
                     but the name gives no direction -- caller should resolve
                     per-entry using the parent voucher's type, see
                     resolve_ambiguous_direction() below.
      None        -- not recognisable as a GST ledger at all.

    tax_type is one of "CGST"/"SGST"/"IGST"/"UTGST"/"CESS", or None when
    direction is None.

    `parent` (the ledger's Group) isn't used as a hard gate -- Tally
    companies routinely nest GST ledgers under custom sub-groups (e.g.
    "Duties & Taxes > GST Payable") rather than directly under
    "Duties & Taxes", so a strict parent-group check would drop real GST
    ledgers whenever a firm's chart of accounts uses sub-groups. It's kept
    as a parameter (rather than dropped) so a future pass can tighten this
    once real Tally company data shows how much it matters in practice.
    """
    name_u = (name or "").upper()

    tax_type = None
    for label, pattern in _TAX_TYPE_PATTERNS:
        if pattern.search(name_u):
            tax_type = label
            break
    if tax_type is None:
        return None, None

    if _GST_OUTPUT_KEYWORDS.search(name_u):
        return "output", tax_type
    if _GST_INPUT_KEYWORDS.search(name_u):
        return "input", tax_type
    return "ambiguous", tax_type


def resolve_ambiguous_direction(voucher_type: str) -> str | None:
    """For a GST ledger entry classified "ambiguous" by name alone, resolve
    Output vs Input from the Voucher Type of its parent voucher (already
    carried on every row fetch_vouchers()/extract() produce -- no new fetch
    needed). Returns None when the voucher type itself doesn't indicate a
    side (Journal, Payment, Receipt, Contra, ...) -- these stay unclassified
    rather than being guessed.
    """
    vt = (voucher_type or "").strip().lower()
    if vt in _OUTPUT_VOUCHER_TYPES:
        return "output"
    if vt in _INPUT_VOUCHER_TYPES:
        return "input"
    return None


# --------------------------------------------------------------------------
# TDS ledger classification
# --------------------------------------------------------------------------
_TDS_NAME_PATTERN = re.compile(r"\bTDS\b", re.I)

# Ordered so the most specific/common section keywords are tried first.
# Best-effort only -- a ledger named e.g. "TDS Payable" with no further
# qualifier legitimately has no section this can infer, and comes back
# "Unclassified" rather than a guess.
_TDS_SECTION_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("194C - Contractors", ("CONTRACT", "194C")),
    ("194J - Professional/Technical Fees", ("PROFESSIONAL", "TECHNICAL", "CONSULTAN", "194J")),
    ("194I - Rent", ("RENT", "194I")),
    ("194H - Commission/Brokerage", ("COMMISSION", "BROKERAGE", "194H")),
    ("194A - Interest (other than securities)", ("INTEREST", "194A")),
    ("194Q - Purchase of Goods", ("194Q",)),
    ("195 - Payments to Non-Resident", ("NON RESIDENT", "NON-RESIDENT", "195")),
    ("192 - Salary", ("SALARY", "192")),
]


def classify_tds_ledger(name: str, parent: str = "") -> tuple[bool, str | None]:
    """Returns (is_tds_ledger, nature_of_payment). nature_of_payment is a
    best-effort "<section> - <description>" label parsed from the ledger
    name, or None ("Unclassified") when no keyword matches -- never guessed."""
    name_u = (name or "").upper()
    if not _TDS_NAME_PATTERN.search(name_u):
        return False, None
    for label, keywords in _TDS_SECTION_KEYWORDS:
        if any(kw in name_u for kw in keywords):
            return True, label
    return True, None
