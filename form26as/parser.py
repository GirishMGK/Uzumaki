"""Flatten Form 26AS TDS (Part I) and TCS (Part VI) sections into rows.

Both sections share the same nested shape: for each deductor/collector there
is a summary row (name, TAN, totals) followed by one or more transaction rows
(section, dates, amounts). This module walks the loaded grid and emits one
flat ``Transaction`` per transaction row, copying the deductor/collector's
name and TAN onto every transaction and tagging it with a Category (TDS/TCS)
so the result is directly searchable/filterable.

Real TRACES exports label these sections "PART-I" / "PART-VI" (Roman
numerals); older documentation and some exports instead use "PART A" /
"PART B". Both conventions are recognized. Everything else - PART-II
(15G/15H declarations, no tax actually deducted), PART-III/IV/V (TDS on
property/rent/virtual digital assets, which use a different column layout
keyed by a TDS Certificate Number rather than a TAN), and so on - is
explicitly walked past rather than risking a mismatched-column misparse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

from .loader import Grid

# A TAN is 4 letters, 5 digits, 1 letter (e.g. AGRA10192A).
TAN_RE = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")

# TDS/TCS section codes: a numeric family prefix optionally followed by letters
# (e.g. 192, 194A, 194IA, 195, 206CL, 206CR). Deliberately generous.
SECTION_RE = re.compile(r"^(19[0-9]|20[0-9])[A-Z]{0,3}$")

_DATE_FORMATS = ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d")


@dataclass
class Transaction:
    assessment_year: str = ""
    source_file: str = ""
    category: str = ""  # "TDS" or "TCS"
    deductor_sr_no: str = ""
    name_of_deductor: str = ""
    tan_of_deductor: str = ""
    total_amount_paid: Optional[float] = None
    total_tax_deducted: Optional[float] = None
    total_tds_deposited: Optional[float] = None
    txn_sr_no: str = ""
    section: str = ""
    transaction_date: str = ""
    status_of_booking: str = ""
    date_of_booking: str = ""
    remarks: str = ""
    amount_paid: Optional[float] = None
    tax_deducted: Optional[float] = None
    tds_deposited: Optional[float] = None

    def as_row(self) -> List[object]:
        return [
            self.assessment_year,
            self.source_file,
            self.category,
            self.deductor_sr_no,
            self.name_of_deductor,
            self.tan_of_deductor,
            self.total_amount_paid,
            self.total_tax_deducted,
            self.total_tds_deposited,
            self.txn_sr_no,
            self.section,
            _to_date(self.transaction_date) or self.transaction_date,
            self.status_of_booking,
            _to_date(self.date_of_booking) or self.date_of_booking,
            self.remarks,
            self.amount_paid,
            self.tax_deducted,
            self.tds_deposited,
        ]


COLUMNS = [
    "Assessment Year",
    "Source File",
    "Category",
    "Deductor/Collector Sr. No.",
    "Name of Deductor/Collector",
    "TAN of Deductor/Collector",
    "Total Amount Paid/Credited",
    "Total Tax Deducted/Collected",
    "Total TDS/TCS Deposited",
    "Txn Sr. No.",
    "Section",
    "Transaction Date",
    "Status of Booking",
    "Date of Booking",
    "Remarks",
    "Amount Paid/Credited",
    "Tax Deducted/Collected",
    "TDS/TCS Deposited",
]

ASSESSMENT_YEAR_RE = re.compile(r"^(20\d{2})-(\d{2})$")
_YEAR_IN_TEXT_RE = re.compile(r"(20\d{2}-\d{2})")
_YEAR_IN_NAME_RE = re.compile(r"(20\d{2}-\d{2}|20\d{2})")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _to_number(text: str) -> Optional[float]:
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    if s in ("", "-", "nan", "none"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_date(text: str) -> Optional[date]:
    if not text:
        return None
    s = str(text).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def detect_assessment_year(grid: Grid) -> Optional[str]:
    """Find the statement's Assessment Year (e.g. '2026-27') inside the grid."""
    for cells in grid:
        for idx, cell in enumerate(cells):
            if _norm(cell) == "assessment year":
                for later in cells[idx + 1:]:
                    if ASSESSMENT_YEAR_RE.match(later.strip()):
                        return later.strip()
        joined = " ".join(cells)
        if "assessment year" in _norm(joined):
            m = _YEAR_IN_TEXT_RE.search(joined)
            if m:
                return m.group(1)
    return None


def year_from_filename(path) -> str:
    """Best-effort fallback: pull a year/FY out of the file name."""
    from pathlib import Path as _Path

    m = _YEAR_IN_NAME_RE.search(_Path(path).stem)
    return m.group(1) if m else "Unknown"


def _find_col(cells: List[str], *keywords: str) -> Optional[int]:
    """First column whose normalized text contains every keyword."""
    for idx, cell in enumerate(cells):
        text = _norm(cell)
        if text and all(k in text for k in keywords):
            return idx
    return None


def _find_col_any(cells: List[str], *keyword_sets: tuple) -> Optional[int]:
    """First column matching any of several alternative keyword combinations."""
    for keywords in keyword_sets:
        idx = _find_col(cells, *keywords)
        if idx is not None:
            return idx
    return None


@dataclass
class _DeductorHeader:
    sr_no: Optional[int] = None
    name: Optional[int] = None
    tan: Optional[int] = None
    total_amount: Optional[int] = None
    total_tax: Optional[int] = None
    total_tds: Optional[int] = None


@dataclass
class _TxnHeader:
    sr_no: Optional[int] = None
    section: Optional[int] = None
    txn_date: Optional[int] = None
    status: Optional[int] = None
    booking_date: Optional[int] = None
    remarks: Optional[int] = None
    amount: Optional[int] = None
    tax: Optional[int] = None
    tds: Optional[int] = None


def _detect_deductor_header(cells: List[str]) -> Optional[_DeductorHeader]:
    # TDS calls this "Name of Deductor" / "TAN of Deductor"; TCS calls the
    # identical layout "Name of Collector" / "TAN of Collector".
    name = _find_col_any(cells, ("name", "deductor"), ("name", "collector"))
    tan = _find_col(cells, "tan")
    if name is None or tan is None:
        return None
    return _DeductorHeader(
        sr_no=_find_col(cells, "sr"),
        name=name,
        tan=tan,
        total_amount=_find_col(cells, "total", "amount"),
        total_tax=_find_col(cells, "total", "tax"),
        total_tds=_find_col_any(cells, ("total", "tds"), ("total", "tcs")),
    )


def _detect_txn_header(cells: List[str]) -> Optional[_TxnHeader]:
    section = _find_col(cells, "section")
    txn_date = _find_col(cells, "transaction", "date")
    if section is None or txn_date is None:
        return None
    return _TxnHeader(
        sr_no=_find_col(cells, "sr"),
        section=section,
        txn_date=txn_date,
        status=_find_col(cells, "status", "booking"),
        booking_date=_find_col(cells, "date", "booking"),
        remarks=_find_col(cells, "remark"),
        amount=_find_col(cells, "amount", "paid"),
        # TDS calls this "Tax Deducted" / "TDS Deposited"; TCS calls the
        # identical column "Tax Collected" / "TCS Deposited".
        tax=_find_col_any(cells, ("tax", "deducted"), ("tax", "collected")),
        tds=_find_col_any(cells, ("tds", "deposited"), ("tcs", "deposited")),
    )


def _get(cells: List[str], idx: Optional[int]) -> str:
    if idx is None or idx < 0 or idx >= len(cells):
        return ""
    return cells[idx]


# Real TRACES exports label sections with Roman numerals ("PART-I", "PART-VI");
# some documentation and older exports use letters ("PART A", "PART B"). Both
# are recognized. Anything else (PART-II 15G/15H, PART-III/IV/V property/
# rent/virtual-digital-asset TDS, etc.) is classified "OTHER" and skipped -
# those use a materially different column layout (a TDS Certificate Number
# instead of a TAN) that this generic TDS/TCS row-shape can't safely parse.
_PART_TOKEN_RE = re.compile(r"\bpart[\s\-]*([a-z0-9]+)", re.IGNORECASE)
_TDS_PART_TOKENS = {"I", "A"}
_TCS_PART_TOKENS = {"VI", "B"}


def _part_category(cells: List[str]) -> Optional[str]:
    """Return 'TDS'/'TCS'/'OTHER' if this row announces a new part, else None."""
    joined = " ".join(cells)
    m = _PART_TOKEN_RE.search(joined)
    if not m:
        return None
    token = m.group(1).upper()
    if token in _TDS_PART_TOKENS:
        return "TDS"
    if token in _TCS_PART_TOKENS:
        return "TCS"
    return "OTHER"


def parse_transactions(grid: Grid) -> List[Transaction]:
    """Parse the TDS (Part I) and TCS (Part VI) sections of a 26AS grid."""
    dhdr: Optional[_DeductorHeader] = None
    thdr: Optional[_TxnHeader] = None
    current: Optional[Transaction] = None
    # "OTHER" until a recognized TDS/TCS part boundary is seen. Files without
    # explicit part markers (e.g. some minimal Excel/HTML exports) never see
    # one, so default to TDS to preserve behavior for those simple exports.
    category = "TDS"
    out: List[Transaction] = []

    for cells in grid:
        marker = _part_category(cells)
        if marker is not None:
            category = marker
            continue

        in_scope = category in ("TDS", "TCS")

        # Refresh header maps whenever a header row appears (column positions
        # can differ between parts, and between TDS's and TCS's own layout).
        maybe_dhdr = _detect_deductor_header(cells)
        if maybe_dhdr is not None:
            dhdr = maybe_dhdr
            continue
        maybe_thdr = _detect_txn_header(cells)
        if maybe_thdr is not None:
            thdr = maybe_thdr
            continue

        # Deductor/collector summary row: a TAN sitting in the TAN column.
        tan_val = _get(cells, dhdr.tan) if dhdr else ""
        if in_scope and dhdr and TAN_RE.match(tan_val.upper()):
            current = Transaction(
                category=category,
                deductor_sr_no=_get(cells, dhdr.sr_no),
                name_of_deductor=_get(cells, dhdr.name),
                tan_of_deductor=tan_val.upper(),
                total_amount_paid=_to_number(_get(cells, dhdr.total_amount)),
                total_tax_deducted=_to_number(_get(cells, dhdr.total_tax)),
                total_tds_deposited=_to_number(_get(cells, dhdr.total_tds)),
            )
            continue

        # Transaction row: a valid section code in the section column.
        section_val = _get(cells, thdr.section) if thdr else ""
        if (
            in_scope
            and thdr
            and current
            and current.category == category
            and SECTION_RE.match(section_val.upper())
        ):
            out.append(
                Transaction(
                    category=category,
                    deductor_sr_no=current.deductor_sr_no,
                    name_of_deductor=current.name_of_deductor,
                    tan_of_deductor=current.tan_of_deductor,
                    total_amount_paid=current.total_amount_paid,
                    total_tax_deducted=current.total_tax_deducted,
                    total_tds_deposited=current.total_tds_deposited,
                    txn_sr_no=_get(cells, thdr.sr_no),
                    section=section_val.upper(),
                    transaction_date=_get(cells, thdr.txn_date),
                    status_of_booking=_get(cells, thdr.status),
                    date_of_booking=_get(cells, thdr.booking_date),
                    remarks=_get(cells, thdr.remarks),
                    amount_paid=_to_number(_get(cells, thdr.amount)),
                    tax_deducted=_to_number(_get(cells, thdr.tax)),
                    tds_deposited=_to_number(_get(cells, thdr.tds)),
                )
            )

    return out
