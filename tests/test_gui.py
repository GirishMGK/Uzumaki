"""Tests for the GUI launcher's headless conversion core (no Tkinter needed)."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from form26as.gui import convert

SAMPLE_TXT = Path(__file__).resolve().parent.parent / "samples" / "sample_26as.txt"


class TestConvert(unittest.TestCase):
    def test_convert_writes_workbook_next_to_input(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "26AS.txt"
            shutil.copy(SAMPLE_TXT, src)
            out_path, count = convert(src)
            self.assertTrue(out_path.exists())
            self.assertEqual(out_path.name, "26AS_formatted.xlsx")
            self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
