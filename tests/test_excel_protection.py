"""Tests for password-protected and legacy-format Excel handling.

These cover a real bug: previously, a password-protected (or legacy binary
.xls) Excel file made openpyxl raise zipfile.BadZipFile, which the loader's
broad except silently retried as HTML - BeautifulSoup found no <table> in the
binary data and returned an EMPTY grid with no error at all, so the tool
quietly reported "no transactions" instead of the real problem.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from form26as.loader import EncryptedExcelError, LegacyExcelError, load_grid
from form26as.parser import parse_transactions

try:
    from openpyxl import Workbook
    import msoffcrypto

    HAVE_MSOFFCRYPTO = True
except Exception:  # pragma: no cover - environment without msoffcrypto
    HAVE_MSOFFCRYPTO = False

try:
    import xlwt

    HAVE_XLWT = True
except Exception:  # pragma: no cover - environment without xlwt
    HAVE_XLWT = False

_ROWS = [
    ["Sr. No.", "Name of Deductor", "TAN of Deductor", "Total Amount Paid / Credited",
     "Total Tax Deducted", "Total TDS Deposited"],
    ["Sr. No.", "Section", "Transaction Date", "Status of Booking", "Date of Booking",
     "Remarks", "Amount Paid / Credited", "Tax Deducted", "TDS Deposited"],
    [1, "ANIL KUMAR GANDHI", "AGRA10192A", 299269.11, 29926.00, 29926.00],
    [1, "194A", "31-Mar-2026", "F", "01-Jun-2026", "-", 18786.32, 1879.00, 1879.00],
]


def _build_encrypted_xlsx(path: Path, password: str) -> None:
    wb = Workbook()
    ws = wb.active
    for row in _ROWS:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    with open(path, "wb") as out:
        msoffcrypto.OfficeFile(buf).encrypt(password, out)


@unittest.skipUnless(HAVE_MSOFFCRYPTO, "openpyxl/msoffcrypto-tool not installed")
class TestEncryptedExcel(unittest.TestCase):
    def test_correct_password_decrypts_and_parses(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "26AS.xlsx"
            _build_encrypted_xlsx(path, "15041985")
            grid = load_grid(path, password="15041985")
            txns = parse_transactions(grid)
        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0].name_of_deductor, "ANIL KUMAR GANDHI")
        self.assertEqual(txns[0].amount_paid, 18786.32)

    def test_missing_password_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "26AS.xlsx"
            _build_encrypted_xlsx(path, "15041985")
            with self.assertRaises(EncryptedExcelError):
                load_grid(path)

    def test_wrong_password_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "26AS.xlsx"
            _build_encrypted_xlsx(path, "15041985")
            with self.assertRaises(EncryptedExcelError):
                load_grid(path, password="01011999")

    def test_no_silent_empty_grid(self):
        """Regression guard: this must never come back as an empty grid."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "26AS.xlsx"
            _build_encrypted_xlsx(path, "15041985")
            with self.assertRaises(Exception):
                load_grid(path)  # no password given


@unittest.skipUnless(HAVE_XLWT, "xlwt not installed")
class TestLegacyXls(unittest.TestCase):
    def test_legacy_binary_xls_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "26AS.xls"
            wb = xlwt.Workbook()
            ws = wb.add_sheet("Sheet1")
            ws.write(0, 0, "Name of Deductor")
            ws.write(0, 1, "TAN of Deductor")
            wb.save(str(path))

            with self.assertRaises(LegacyExcelError):
                load_grid(path)


if __name__ == "__main__":
    unittest.main()
