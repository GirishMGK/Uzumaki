"""End-to-end tests for the Form 26AS parser using a synthetic sample."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from form26as.loader import load_grid
from form26as.parser import parse_transactions

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SAMPLE = SAMPLES / "sample_26as.html"
SAMPLE_TXT = SAMPLES / "sample_26as.txt"


class TestParser(unittest.TestCase):
    def setUp(self):
        self.txns = parse_transactions(load_grid(SAMPLE))

    def test_transaction_count(self):
        # 2 transactions under the first deductor + 1 under the second.
        self.assertEqual(len(self.txns), 3)

    def test_deductor_attached_to_every_txn(self):
        first = self.txns[0]
        self.assertEqual(first.category, "TDS")
        self.assertEqual(first.name_of_deductor, "ANIL KUMAR GANDHI")
        self.assertEqual(first.tan_of_deductor, "AGRA10192A")
        self.assertEqual(first.section, "194A")
        self.assertEqual(first.transaction_date, "31-Mar-2026")
        self.assertEqual(first.status_of_booking, "F")
        self.assertEqual(first.date_of_booking, "01-Jun-2026")
        self.assertEqual(first.amount_paid, 18786.32)
        self.assertEqual(first.tax_deducted, 1879.00)
        self.assertEqual(first.tds_deposited, 1879.00)
        self.assertEqual(first.total_amount_paid, 299269.11)

    def test_indian_number_format_parsed(self):
        # "2,80,482.79" must parse to a float.
        second = self.txns[1]
        self.assertEqual(second.amount_paid, 280482.79)

    def test_second_deductor(self):
        third = self.txns[2]
        self.assertEqual(third.name_of_deductor, "STATE BANK OF INDIA")
        self.assertEqual(third.tan_of_deductor, "MUMS12345B")
        self.assertEqual(third.amount_paid, 50000.00)


class TestCaretDelimitedText(unittest.TestCase):
    """Covers a file with explicit PART markers: PART A (TDS), PART A1
    (15G/15H - no tax actually deducted), and PART B (TCS)."""

    def setUp(self):
        self.txns = parse_transactions(load_grid(SAMPLE_TXT))

    def test_tds_and_tcs_collected_other_parts_excluded(self):
        # 3 PART A (TDS) transactions + 1 PART B (TCS) transaction.
        # PART A1 (15G/15H) must be excluded - no tax was actually deducted.
        self.assertEqual(len(self.txns), 4)
        tans = {t.tan_of_deductor for t in self.txns}
        self.assertIn("AGRA10192A", tans)
        self.assertIn("MUMS12345B", tans)
        self.assertIn("JPRS55555Q", tans)  # PART B (TCS) - now included
        self.assertNotIn("DELS99999Z", tans)  # PART A1 (15G/15H) - excluded

    def test_categories_are_tagged_correctly(self):
        by_tan = {t.tan_of_deductor: t.category for t in self.txns}
        self.assertEqual(by_tan["AGRA10192A"], "TDS")
        self.assertEqual(by_tan["MUMS12345B"], "TDS")
        self.assertEqual(by_tan["JPRS55555Q"], "TCS")

    def test_tcs_fields_parsed(self):
        tcs = next(t for t in self.txns if t.tan_of_deductor == "JPRS55555Q")
        self.assertEqual(tcs.name_of_deductor, "SOME MOTORS LTD")
        self.assertEqual(tcs.section, "206CE")
        self.assertEqual(tcs.amount_paid, 1000000.00)
        self.assertEqual(tcs.tax_deducted, 10000.00)
        self.assertEqual(tcs.tds_deposited, 10000.00)

    def test_fields_from_text(self):
        first = self.txns[0]
        self.assertEqual(first.name_of_deductor, "ANIL KUMAR GANDHI")
        self.assertEqual(first.section, "194A")
        self.assertEqual(first.amount_paid, 18786.32)
        self.assertEqual(first.tds_deposited, 1879.00)


if __name__ == "__main__":
    unittest.main()
