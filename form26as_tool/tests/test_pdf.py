"""PDF loading tests. Generates a synthetic 26AS-style PDF with reportlab and
parses it back with pdfplumber. Skips cleanly if either library is unavailable.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from form26as.loader import load_grid
from form26as.parser import parse_part_a

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.pdfencrypt import StandardEncryption
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    import pdfplumber  # noqa: F401

    HAVE_PDF = True
except Exception:  # pragma: no cover - environment without PDF libs
    HAVE_PDF = False

_ROWS = [
    ["Sr. No.", "Name of Deductor", "TAN of Deductor", "Total Amount Paid / Credited",
     "Total Tax Deducted", "Total TDS Deposited", "", "", ""],
    ["Sr. No.", "Section", "Transaction Date", "Status of Booking", "Date of Booking",
     "Remarks", "Amount Paid / Credited", "Tax Deducted", "TDS Deposited"],
    ["1", "ANIL KUMAR GANDHI", "AGRA10192A", "299269.11", "29926.00", "29926.00", "", "", ""],
    ["1", "194A", "31-Mar-2026", "F", "01-Jun-2026", "-", "18786.32", "1879.00", "1879.00"],
    ["2", "194A", "31-Dec-2025", "F", "10-Feb-2026", "-", "280482.79", "28047.00", "28047.00"],
    ["2", "STATE BANK OF INDIA", "MUMS12345B", "50000.00", "5000.00", "5000.00", "", "", ""],
    ["1", "194A", "30-Jun-2025", "F", "15-Jul-2025", "-", "50000.00", "5000.00", "5000.00"],
]


def _build_pdf(path, password=None):
    styles = getSampleStyleSheet()
    kwargs = {}
    if password:
        kwargs["encrypt"] = StandardEncryption(userPassword=password)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), **kwargs)
    table = Table(_ROWS)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
    ]))
    doc.build([Paragraph("PART A - Details of Tax Deducted at Source", styles["Heading2"]),
               table])


@unittest.skipUnless(HAVE_PDF, "reportlab/pdfplumber not installed")
class TestPdf(unittest.TestCase):
    def test_plain_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "s.pdf"
            _build_pdf(pdf)
            txns = parse_part_a(load_grid(pdf))
        self.assertEqual(len(txns), 3)
        self.assertEqual(txns[0].name_of_deductor, "ANIL KUMAR GANDHI")
        self.assertEqual(txns[0].tan_of_deductor, "AGRA10192A")
        self.assertEqual(txns[0].amount_paid, 18786.32)
        self.assertEqual(txns[2].name_of_deductor, "STATE BANK OF INDIA")

    def test_encrypted_pdf_with_password(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "enc.pdf"
            _build_pdf(pdf, password="15041985")
            txns = parse_part_a(load_grid(pdf, password="15041985"))
        self.assertEqual(len(txns), 3)
        self.assertEqual(txns[1].amount_paid, 280482.79)


if __name__ == "__main__":
    unittest.main()
