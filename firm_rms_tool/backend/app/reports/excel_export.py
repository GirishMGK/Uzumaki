"""Shared "formatted .xlsx export" builder (§10, §11).

Every grid/chart export in the app goes through `build_formatted_workbook`:
frozen header row, auto-width columns, and the Indian numbering format
(##,##,##0.00 lakh/crore grouping) on any column flagged as money — exactly
the format string given in §10.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

import xlsxwriter

INDIAN_NUMBER_FORMAT = "[>=10000000]##\\,##\\,##\\,##0.00;[>=100000]##\\,##\\,##0.00;##,##0.00"


@dataclass
class ColumnSpec:
    key: str
    header: str
    kind: str = "text"  # "text" | "number" | "money" | "pct"


def build_formatted_workbook(rows: list[dict[str, Any]], columns: list[ColumnSpec], *, sheet_name: str = "Data") -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    # Excel forbids [ ] : * ? / \ in a sheet name and caps it at 31 chars —
    # report titles like "Location/Office Resourcing" hit this directly.
    safe_sheet_name = re.sub(r"[\[\]:*?/\\]", "-", sheet_name)[:31] or "Data"
    ws = wb.add_worksheet(safe_sheet_name)

    header_fmt = wb.add_format({"bold": True, "bg_color": "#0f172a", "font_color": "white", "border": 1})
    money_fmt = wb.add_format({"num_format": INDIAN_NUMBER_FORMAT})
    number_fmt = wb.add_format({"num_format": "#,##0.00"})
    pct_fmt = wb.add_format({"num_format": "0.0%"})

    for col_idx, col in enumerate(columns):
        ws.write(0, col_idx, col.header, header_fmt)

    widths = [max(len(col.header), 10) for col in columns]
    for row_idx, row in enumerate(rows, start=1):
        for col_idx, col in enumerate(columns):
            value = row.get(col.key)
            if col.kind == "money" and value is not None:
                ws.write_number(row_idx, col_idx, float(value), money_fmt)
            elif col.kind == "number" and value is not None:
                ws.write_number(row_idx, col_idx, float(value), number_fmt)
            elif col.kind == "pct" and value is not None:
                ws.write_number(row_idx, col_idx, float(value) / 100, pct_fmt)
            else:
                ws.write(row_idx, col_idx, "" if value is None else value)
            widths[col_idx] = max(widths[col_idx], len(str(value)) if value is not None else 0)

    for col_idx, width in enumerate(widths):
        ws.set_column(col_idx, col_idx, min(width + 2, 50))
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, max(len(rows), 1), len(columns) - 1)

    wb.close()
    return buf.getvalue()
