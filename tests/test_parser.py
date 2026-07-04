"""End-to-end tests for the Form 26AS parser using a synthetic sample."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from form26as.loader import load_grid
from form26as.parser import _part_category, parse_transactions

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


class TestPartBoundaryDetection(unittest.TestCase):
    """Regression test: a deductor literally named "PARTH ..." (or PARTNERS,
    APARTMENT, DEPARTMENT, PARTAP, ...) must never be mistaken for a
    "PART <token>" section-boundary announcement. A bare substring match on
    "part" once did exactly that and silently dropped every row after it."""

    def test_names_containing_part_are_not_boundaries(self):
        names = [
            "PARTH UPADHYAY", "PARTNERS ENTERPRISES", "APARTMENT ASSOCIATION",
            "DEPARTMENT OF POST", "PARTAP SINGH", "PARTHASARATHY IYER",
        ]
        for name in names:
            row = ["98", name, "AHMP15771A", "", "", "62990", "6299", "6299"]
            self.assertIsNone(_part_category(row), f"false positive on {name!r}")

    def test_real_part_headers_still_detected(self):
        cases = [
            ("PART-I - Details of Tax Deducted at Source", "TDS"),
            ("PART-VI - Details of Tax Collected at Source", "TCS"),
            ("PART A - Details of Tax Deducted at Source", "TDS"),
            ("PART A1 - Details of Tax Deducted at Source for 15G/15H", "OTHER"),
            ("PART B - Details of Tax Collected at Source", "TCS"),
            ("PART-III - Details of Transactions under Proviso to 194IA", "OTHER"),
        ]
        for text, expected in cases:
            self.assertEqual(_part_category([text]), expected, text)

    def test_large_deductor_list_with_part_like_name_not_truncated(self):
        """End-to-end: a PART-I list of 100 deductors, one of them named
        "PARTH ..." partway through, followed by a real PART-VI/TCS section -
        all 100 TDS deductors plus the TCS entry must come through."""
        grid = [
            ["PART-I - Details of Tax Deducted at Source"],
            ["Sr. No.", "Name of Deductor", "TAN of Deductor", "", "",
             "Total Amount Paid/Credited(Rs.)", "Total Tax Deducted(Rs.)", "Total TDS Deposited(Rs.)"],
        ]
        for i in range(1, 101):
            name = "PARTH UPADHYAY" if i == 98 else f"DEDUCTOR {i}"
            tan = f"AHMP{10000 + i}{'F' if i % 2 else 'A'}"
            grid.append([str(i), name, tan, "", "", "10000", "1000", "1000"])
            grid.append(["Sr. No.", "Section", "Transaction Date", "Status of Booking",
                         "Date of Booking", "Remarks", "Amount Paid/Credited(Rs.)",
                         "Tax Deducted(Rs.)", "TDS Deposited(Rs.)"])
            grid.append(["1", "194A", "01-Jan-2024", "F", "05-Jan-2024", "-", "10000", "1000", "1000"])
        grid.append(["PART-VI - Details of Tax Collected at Source"])
        grid.append(["Sr. No.", "Name of Collector", "TAN of Collector", "", "",
                     "Total Amount Paid/Debited(Rs.)", "Total Tax Collected(Rs.)", "Total TCS Deposited(Rs.)"])
        grid.append(["1", "SOME COLLECTOR", "DELA50336G", "", "", "5000", "500", "500"])
        grid.append(["Sr. No.", "Section", "Transaction Date", "Status of Booking",
                     "Date of Booking", "Remarks", "Amount Paid/Debited(Rs.)",
                     "Tax Collected(Rs.)", "TCS Deposited(Rs.)"])
        grid.append(["1", "206CL", "10-Jan-2024", "F", "15-Jan-2024", "-", "5000", "500", "500"])

        txns = parse_transactions(grid)
        tds = [t for t in txns if t.category == "TDS"]
        tcs = [t for t in txns if t.category == "TCS"]
        self.assertEqual(len(tds), 100, "deductors after the PARTH entry were dropped")
        self.assertEqual(len(tcs), 1)
        self.assertIn("PARTH UPADHYAY", {t.name_of_deductor for t in tds})
        self.assertIn("DEDUCTOR 100", {t.name_of_deductor for t in tds})


if __name__ == "__main__":
    unittest.main()
