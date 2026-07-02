"""Tests for the GUI launcher's headless conversion core (no Tkinter needed)."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from form26as.merge import parse_files
from form26as.writer import write_xlsx

SAMPLE_TXT = Path(__file__).resolve().parent.parent / "samples" / "sample_26as.txt"
SAMPLE_HTML = Path(__file__).resolve().parent.parent / "samples" / "sample_26as.html"


class TestGuiCore(unittest.TestCase):
    def test_single_file_writes_workbook(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "26AS.txt"
            shutil.copy(SAMPLE_TXT, src)
            transactions, results = parse_files([src])
            self.assertEqual(len(transactions), 3)
            self.assertEqual(results[0]["count"], 3)
            self.assertIsNone(results[0]["error"])

            out_path = src.with_name("26AS_formatted.xlsx")
            write_xlsx(transactions, out_path)
            self.assertTrue(out_path.exists())

    def test_multiple_files_combine_with_year_tags(self):
        with tempfile.TemporaryDirectory() as d:
            f1 = Path(d) / "26AS_2024-25.txt"
            f2 = Path(d) / "26AS_other_year.html"
            shutil.copy(SAMPLE_TXT, f1)
            shutil.copy(SAMPLE_HTML, f2)

            transactions, results = parse_files([f1, f2])
            self.assertEqual(len(transactions), 6)  # 3 + 3
            self.assertEqual({r["path"] for r in results}, {f1, f2})

            # f1 carries the assessment year embedded in the file, f2 falls
            # back to a year-like token in its filename (none here -> Unknown).
            years = {t.assessment_year for t in transactions if t.source_file == f1.name}
            self.assertEqual(years, {"2026-27"})

            out_path = Path(d) / "26AS_Combined_formatted.xlsx"
            write_xlsx(transactions, out_path)
            self.assertTrue(out_path.exists())

    def test_one_bad_file_does_not_abort_the_batch(self):
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "good.txt"
            shutil.copy(SAMPLE_TXT, good)
            missing = Path(d) / "does_not_exist.txt"

            transactions, results = parse_files([good, missing])
            self.assertEqual(len(transactions), 3)
            by_path = {r["path"]: r for r in results}
            self.assertIsNotNone(by_path[missing]["error"])
            self.assertIsNone(by_path[good]["error"])


if __name__ == "__main__":
    unittest.main()
