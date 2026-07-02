"""Flatten Form 26AS Part A (Details of Tax Deducted at Source) into rows.

Part A is nested: for each deductor there is a summary row (name, TAN, totals)
followed by one or more transaction rows (section, dates, amounts). This module
walks the loaded grid and emits one flat ``Transaction`` per transaction row,
copying the deductor's name and TAN onto every transaction so the result is
directly searchable/filterable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

from .loader import Grid

# A TAN is 4 letters, 5 digits, 1 letter (e.g. AGRA10192A).
TAN_RE = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")

# TDS/TCS section codes: a numeric family prefix optionally followed by letters
# (e.g. 192, 194A, 194IA, 195, 206CA). Deliberately generous.
SECTION_RE = re.compile(r"^(19[0-9]|20[0-9])[A-Z]{0,3}$")

_DATE_FORMATS = ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d")


@dataclass
class Transaction:
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
    "Deductor Sr. No.",
    "Name of Deductor",
    "TAN of Deductor",
    "Total Amount Paid/Credited",
    "Total Tax Deducted",
    "Total TDS Deposited",
    "Txn Sr. No.",
    "Section",
    "Transaction Date",
    "Status of Booking",
    "Date of Booking",
    "Remarks",
    "Amount Paid/Credited",
    "Tax Deducted",
    "TDS Deposited",
]


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


def _find_col(cells: List[str], *keywords: str) -> Optional[int]:
    """First column whose normalized text contains every keyword."""
    for idx, cell in enumerate(cells):
        text = _norm(cell)
        if text and all(k in text for k in keywords):
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
    name = _find_col(cells, "name", "deductor")
    tan = _find_col(cells, "tan")
    if name is None or tan is None:
        return None
    return _DeductorHeader(
        sr_no=_find_col(cells, "sr"),
        name=name,
        tan=tan,
        total_amount=_find_col(cells, "total", "amount"),
        total_tax=_find_col(cells, "total", "tax"),
        total_tds=_find_col(cells, "total", "tds"),
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
        tax=_find_col(cells, "tax", "deducted"),
        tds=_find_col(cells, "tds", "deposited"),
    )


def _get(cells: List[str], idx: Optional[int]) -> str:
    if idx is None or idx < 0 or idx >= len(cells):
        return ""
    return cells[idx]


def parse_part_a(grid: Grid) -> List[Transaction]:
    """Parse Part A of a loaded 26AS grid into flat transaction rows."""
    dhdr: Optional[_DeductorHeader] = None
    thdr: Optional[_TxnHeader] = None
    current: Optional[Transaction] = None
    out: List[Transaction] = []

    for cells in grid:
        # Refresh header maps whenever a header row appears (Part A headers may
        # repeat, and column positions can differ between parts).
        maybe_dhdr = _detect_deductor_header(cells)
        if maybe_dhdr is not None:
            dhdr = maybe_dhdr
            continue
        maybe_thdr = _detect_txn_header(cells)
        if maybe_thdr is not None:
            thdr = maybe_thdr
            continue

        # Deductor summary row: a TAN sitting in the TAN column.
        tan_val = _get(cells, dhdr.tan) if dhdr else ""
        if dhdr and TAN_RE.match(tan_val.upper()):
            current = Transaction(
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
        if thdr and current and SECTION_RE.match(section_val.upper()):
            out.append(
                Transaction(
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
