"""P1 DoD (§13): "Import 300 staff and 200 clients from the sample workbook
with a clean error report."
"""
from pathlib import Path

from app.importers.base import read_workbook_rows
from app.importers.clients_importer import validate_client_rows
from app.importers.staff_importer import validate_staff_rows

SAMPLE_PATH = Path(__file__).parent.parent / "seed" / "sample_masters.xlsx"


def test_sample_workbook_staff_sheet_imports_clean():
    rows = read_workbook_rows(SAMPLE_PATH.read_bytes(), sheet_name="staff")
    assert len(rows) == 300
    result = validate_staff_rows(rows)
    assert len(result.errors) == 0
    assert len(result.valid_rows) == 300


def test_sample_workbook_clients_sheet_imports_clean():
    rows = read_workbook_rows(SAMPLE_PATH.read_bytes(), sheet_name="clients")
    assert len(rows) == 200
    result = validate_client_rows(rows)
    assert len(result.valid_rows) == 200
    assert len(result.errors) == 0
