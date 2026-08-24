"""Print-ready PDF export via reportlab platypus (§1, §11).

Companion to `excel_export.py` — same `ColumnSpec` list, same Indian
number formatting for money columns, but rendered as a landscape A4 table
suitable for a partner meeting printout.
"""
from __future__ import annotations

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.reports.excel_export import ColumnSpec


def _format_indian(value: float) -> str:
    """Approximation of §10's Indian digit-grouping (lakh/crore) for PDF cells."""
    negative = value < 0
    value = abs(value)
    whole = int(value)
    frac = round(value - whole, 2)
    s = str(whole)
    if len(s) <= 3:
        grouped = s
    else:
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        grouped = ",".join(parts) + "," + last3
    out = f"{grouped}.{int(round(frac * 100)):02d}"
    return f"-{out}" if negative else out


def build_formatted_pdf(rows: list[dict[str, Any]], columns: list[ColumnSpec], *, title: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), topMargin=1.2 * cm, bottomMargin=1.2 * cm, leftMargin=1 * cm, rightMargin=1 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Heading1"], fontSize=14, spaceAfter=6)
    meta_style = ParagraphStyle("ReportMeta", parent=styles["Normal"], fontSize=8, textColor=colors.grey)

    header = [col.header for col in columns]
    body = []
    for row in rows:
        line = []
        for col in columns:
            value = row.get(col.key)
            if value is None:
                line.append("")
            elif col.kind == "money":
                line.append(f"Rs. {_format_indian(float(value))}")
            elif col.kind == "pct":
                line.append(f"{float(value):.1f}%")
            elif col.kind == "number":
                line.append(f"{float(value):,.2f}" if isinstance(value, float) else str(value))
            else:
                line.append(str(value))
        body.append(line)

    table_data = [header] + body
    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    elements = [
        Paragraph(title, title_style),
        Paragraph(f"{len(rows)} rows", meta_style),
        Spacer(1, 8),
        table,
    ]
    doc.build(elements)
    return buf.getvalue()
