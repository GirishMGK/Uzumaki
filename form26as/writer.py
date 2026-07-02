"""Write parsed transactions to a formatted, searchable .xlsx and/or .csv."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import List

from .parser import COLUMNS, Transaction

_NUMERIC_COLS = {
    "Total Amount Paid/Credited",
    "Total Tax Deducted",
    "Total TDS Deposited",
    "Amount Paid/Credited",
    "Tax Deducted",
    "TDS Deposited",
}
_DATE_COLS = {"Transaction Date", "Date of Booking"}


def write_csv(transactions: List[Transaction], path: str | Path) -> None:
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for txn in transactions:
            row = []
            for value in txn.as_row():
                if isinstance(value, date):
                    row.append(value.isoformat())
                elif value is None:
                    row.append("")
                else:
                    row.append(value)
            writer.writerow(row)


def write_xlsx(transactions: List[Transaction], path: str | Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    path = Path(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "Form 26AS - Part A"

    header_fill = PatternFill("solid", fgColor="1F6390")
    header_font = Font(bold=True, color="FFFFFF")
    ws.append(COLUMNS)
    for col_idx, _ in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for txn in transactions:
        ws.append(txn.as_row())

    # Apply number and date formats per column.
    for col_idx, name in enumerate(COLUMNS, start=1):
        letter = get_column_letter(col_idx)
        if name in _NUMERIC_COLS:
            number_format = "#,##0.00"
        elif name in _DATE_COLS:
            number_format = "DD-MMM-YYYY"
        else:
            number_format = None
        if number_format:
            for row_idx in range(2, ws.max_row + 1):
                ws.cell(row=row_idx, column=col_idx).number_format = number_format

    # Column widths sized to content (bounded).
    for col_idx, name in enumerate(COLUMNS, start=1):
        letter = get_column_letter(col_idx)
        longest = len(name)
        for row_idx in range(2, ws.max_row + 1):
            value = ws.cell(row=row_idx, column=col_idx).value
            if value is not None:
                longest = max(longest, len(str(value)))
        ws.column_dimensions[letter].width = min(max(longest + 2, 12), 40)

    # Freeze the header and enable AutoFilter so the sheet is searchable/sortable.
    ws.freeze_panes = "A2"
    last_col = get_column_letter(len(COLUMNS))
    ws.auto_filter.ref = f"A1:{last_col}{max(ws.max_row, 1)}"

    wb.save(str(path))
