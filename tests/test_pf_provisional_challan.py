"""Tests for extract_pf_data() (Combined_PF_Statutory.py) against a real
"Provisional Challan" layout -- real feedback, with a real challan
screenshot: "whenever the PDF looks like attached or contains word
Challan, i need the table to be extracted as it is with Establishment
name and Month and generated date. check whether the code contains it as
when im trying to extract its not working properly."

Testing that exact request against a real generated PDF (reportlab,
mimicking the actual challan's layout) surfaced three real bugs, all
fixed here:
  1. Establishment name came back empty -- the company-name regex only
     recognized a plain "Name :" label, not this layout's actual
     "Establishment Code & Name : <code> <name>" label, even though the
     code had an is_new/is_old branch meant to pick between them (this
     layout's "CHALLAN FOR WAGE MONTH" header selected the wrong branch
     for its own "Establishment Code & Name" label).
  2. pdfplumber's own page.extract_text() (used throughout
     extract_pf_data(), not just its extract_tables()) was found to
     corrupt wrapped/tightly-packed table cells: characters from the
     Amount column got interleaved INTO the middle of the Particulars
     label text (e.g. "...Of Contribution" + "533766" garbled into
     "...Of Contribu5t3io3n766"). Switched to the same fitz-based text
     extraction (_read_pdf_text) used everywhere else in this file, which
     doesn't have this failure mode on the same PDF.
  3. Even with clean fitz text, the regex row-parser's label-to-first-
     number boundary required at least one whitespace character, but
     real extracted text sometimes has none at all between adjacent
     cells (e.g. "Contribution533766") -- loosened to \\s* there while
     keeping \\s+ between the numeric tokens themselves.
"""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import pytest

pytest.importorskip("reportlab")

from Combined_PF_Statutory import _detect_pf_type, extract_pf_data


def _build_provisional_challan_pdf(path: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    elements = [
        Paragraph(
            "PROVISIONAL CHALLAN FOR WAGE MONTH : MAR 2026",
            ParagraphStyle("h", parent=styles["Normal"], fontSize=11, fontName="Helvetica-Bold"),
        ),
        Paragraph(
            "EMPLOYEES' PROVIDENT FUND ORGANISATION",
            ParagraphStyle("h2", parent=styles["Normal"], fontSize=13, fontName="Helvetica-Bold", textColor=colors.blue),
        ),
        Paragraph("TRRN : 2604240123926", styles["Normal"]),
        Paragraph("Generated On : 15-Apr-2026 15:25:31", styles["Normal"]),
        Spacer(1, 12),
        Paragraph(
            "Establishment Code & Name : BGBNG2410422000 TENET DIAGNOSTICS PRIVATE LIMITED",
            styles["Normal"],
        ),
        Paragraph("Total Subscribers : 374", styles["Normal"]),
        Spacer(1, 12),
    ]

    table_data = [
        ["SL.", "PARTICULARS", "A/C.01 (Rs.)", "A/C.02 (Rs.)", "A/C.10 (Rs.)", "A/C.21 (Rs.)", "A/C.22 (Rs.)", "TOTAL"],
        ["1", "Employee's Share Of Contribution", "533766", "NA", "NA", "NA", "NA", "533766"],
        ["2", "Employer's Share Of Contribution", "168794", "NA", "364972", "22241", "NA", "556007"],
        ["3", "Admin/ Insp. Charges", "NA", "22240", "NA", "NA", "0", "22240"],
        ["4", "7Q", "0", "0", "0", "0", "0", "0"],
        ["5", "14B", "0", "0", "0", "0", "0", "0"],
    ]
    t = Table(
        table_data,
        colWidths=[0.4 * inch, 1.8 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch, 0.7 * inch],
    )
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    elements += [t, Spacer(1, 6), Paragraph("Grand Total : 1112013", styles["Normal"])]

    SimpleDocTemplate(path, pagesize=A4).build(elements)


def test_provisional_challan_detected_as_challan_type(tmp_path):
    from common.statutory_extractors import read_pdf_text

    pdf_path = str(tmp_path / "provisional_challan.pdf")
    _build_provisional_challan_pdf(pdf_path)
    with open(pdf_path, "rb") as f:
        text = read_pdf_text(f.read())
    assert _detect_pf_type(text) == "CHALLAN"


def test_provisional_challan_header_fields(tmp_path):
    from pathlib import Path

    pdf_path = tmp_path / "provisional_challan.pdf"
    _build_provisional_challan_pdf(str(pdf_path))
    data = extract_pf_data(pdf_path)

    assert data["Company"] == "TENET DIAGNOSTICS PRIVATE LIMITED"
    assert data["Establishment ID"] == "BGBNG2410422000"
    assert data["Month"] == "MAR2026"
    assert data["Generated On"] == "15-Apr-2026 15:25:31"
    assert data["Grand Total"] == "1112013"


def test_provisional_challan_table_extracted_as_is(tmp_path):
    """The actual real-world case this was fixed for: every particulars
    row, with its real per-account Amount/NA values, none corrupted or
    dropped."""
    from pathlib import Path

    pdf_path = tmp_path / "provisional_challan.pdf"
    _build_provisional_challan_pdf(str(pdf_path))
    data = extract_pf_data(pdf_path)

    df = data["Detail Table"]
    assert df is not None
    assert len(df) == 5

    rows = {r["Particulars"]: r for r in df.to_dict("records")}
    assert rows["Employee's Share Of Contribution"]["A/C.01 (Rs.)"] == "533766"
    assert rows["Employee's Share Of Contribution"]["A/C.02 (Rs.)"] == "NA"
    assert rows["Employee's Share Of Contribution"]["Total"] == "533766"
    assert rows["Employer's Share Of Contribution"]["A/C.01 (Rs.)"] == "168794"
    assert rows["Employer's Share Of Contribution"]["A/C.10 (Rs.)"] == "364972"
    assert rows["Employer's Share Of Contribution"]["A/C.21 (Rs.)"] == "22241"
    assert rows["Employer's Share Of Contribution"]["Total"] == "556007"
    assert rows["Admin/ Insp. Charges"]["A/C.02 (Rs.)"] == "22240"
    assert rows["Admin/ Insp. Charges"]["A/C.22 (Rs.)"] == "0"
    assert rows["7Q"]["A/C.01 (Rs.)"] == "0"
    assert rows["14B"]["A/C.01 (Rs.)"] == "0"
