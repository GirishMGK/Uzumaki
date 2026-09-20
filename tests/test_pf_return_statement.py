"""Tests for extract_ecr_return()/(detection) in Combined_PF_Statutory.py
against a real "Return Statement" PDF layout -- real feedback, with a
real screenshot: "TRRN is working fine ... keep the Return format but
information is not captured from it."

Testing that exact request against a real generated PDF (reportlab,
mimicking the actual document) surfaced two real bugs, both fixed here:

1. _detect_pf_type() didn't recognize this document at all -- it's
   titled "RETURN STATEMENT ( Regular Return ) : Sep 2025", which
   matched none of the existing RETURN patterns (all written for a
   different, older "Electronic Challan cum Return" document with
   "ECR Type"/"TRRN Number"/"Return Month" labels this one doesn't have)
   nor any CHALLAN pattern, so it silently fell through to the function's
   final `return "CHALLAN"` default -- meaning extract_ecr_return() was
   never even called on it; the file was routed to the Challan extractor
   instead, matching nothing there either, which is why every cell in
   the Return tab came back empty.

2. extract_ecr_return() itself only recognized the old ECR-Type document's
   label set. Rewritten against this document's real labels (Name of
   Establishment, Establishment Id, LIN, Contribution Rate (%), Return
   File Id, Uploaded Date Time, Total Members, Exemption Status, Remarks,
   Total EPF/EPS/EPF-EPS Contribution, Total Refund of Advances, plus the
   Return Type and Wage Month embedded in the title itself), reading
   directly off the clean fitz text already extracted for detection
   (not a second, corruption-prone pdfplumber re-open).
"""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import pytest

pytest.importorskip("reportlab")

from Combined_PF_Statutory import _detect_pf_type, extract_ecr_return


def _build_return_statement_pdf(path: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    elements = [
        Paragraph(
            "EMPLOYEE'S PROVIDENT FUND ORGANISATION",
            ParagraphStyle("h", parent=styles["Normal"], fontSize=14, fontName="Helvetica-Bold", alignment=1),
        ),
        Paragraph(
            "RETURN STATEMENT  ( Regular Return ) : Sep 2025",
            ParagraphStyle("h2", parent=styles["Normal"], fontSize=12, fontName="Helvetica-Bold", alignment=1),
        ),
        Spacer(1, 12),
    ]

    info_rows = [
        ["Name of Establishment", "TENET DIAGNOSTICS PRIVATE LIMITED", "", ""],
        ["Establishment Id", "BGBNG2410422000", "LIN", "1850010429"],
        ["Contribution Rate (%)", "12", "Return File Id", "251001583150"],
        ["Uploaded Date Time", "14-OCT-2025 16:55", "Total Members", "328"],
        ["Exemption Status", "Unexempted", "", ""],
        ["Remarks", "ok", "", ""],
    ]
    t = Table(info_rows, colWidths=[1.6 * inch, 2 * inch, 1.2 * inch, 1.5 * inch])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    elements += [t, Spacer(1, 12)]

    elements.append(Paragraph(
        "Contribution and Remittance Details (In Rupees) :",
        ParagraphStyle("b", parent=styles["Normal"], fontName="Helvetica-Bold"),
    ))
    cr_rows = [
        ["Total EPF Contribution", "460474", "Total EPS Contribution", "314245"],
        ["Total EPF-EPS Contribution", "146229", "Total Refund of Advances", "0"],
    ]
    t2 = Table(cr_rows, colWidths=[1.8 * inch, 1.6 * inch, 1.8 * inch, 1.2 * inch])
    t2.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    elements.append(t2)

    SimpleDocTemplate(path, pagesize=A4).build(elements)


def test_return_statement_detected_as_return_type(tmp_path):
    from common.statutory_extractors import read_pdf_text

    pdf_path = str(tmp_path / "return_statement.pdf")
    _build_return_statement_pdf(pdf_path)
    with open(pdf_path, "rb") as f:
        text = read_pdf_text(f.read())
    assert _detect_pf_type(text) == "RETURN"


def test_return_statement_fields_extracted(tmp_path):
    from common.statutory_extractors import read_pdf_text

    pdf_path = str(tmp_path / "return_statement.pdf")
    _build_return_statement_pdf(pdf_path)
    with open(pdf_path, "rb") as f:
        text = read_pdf_text(f.read())
    row = extract_ecr_return("return_statement.pdf", text)

    assert row["Return Type"] == "Regular Return"
    assert row["Wage Month"] == "Sep-2025"
    assert row["Name of Establishment"] == "TENET DIAGNOSTICS PRIVATE LIMITED"
    assert row["Establishment Id"] == "BGBNG2410422000"
    assert row["LIN"] == "1850010429"
    assert row["Contribution Rate (%)"] == "12"
    assert row["Return File Id"] == "251001583150"
    assert row["Uploaded Date Time"] == "14-OCT-2025 16:55"
    assert row["Total Members"] == "328"
    assert row["Exemption Status"] == "Unexempted"
    assert row["Remarks"] == "ok"
    assert row["Total EPF Contribution"] == "460474"
    assert row["Total EPS Contribution"] == "314245"
    assert row["Total EPF-EPS Contribution"] == "146229"
    assert row["Total Refund of Advances"] == "0"
