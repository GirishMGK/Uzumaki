"""Builds seed/sample_masters.xlsx: a Data Dictionary sheet plus 300
staff / 200 client demo rows, in the same firm-facing column format as
the in-app "Download template" button (app.importers.templates) and the
Masters page's Add-one form — see app.importers.friendly_values, the
single source of truth all three draw from.

Doubles as a "does the importer accept our own demo data cleanly" smoke
fixture (see tests/test_sample_workbook.py, P1 DoD in §13).

Run: cd backend && python -m seed.generate_sample_workbook
"""
import random
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation

from app.importers.friendly_values import (
    CLIENT_STATUS_MAP,
    DESIGNATION_MAP,
    ENGAGEMENT_TYPE_MAP,
    ENTITY_TYPE_MAP,
    FIRMS,
    NATURE_MAP,
    PARTNERS,
    PRIORITY_MAP,
    STAFF_STATUS_MAP,
    WORK_LOCATIONS,
    YES_NO_MAP,
)
from seed.seed_data import FIRST_NAMES, LAST_NAMES

random.seed(7)

OUT_PATH = Path(__file__).parent / "sample_masters.xlsx"

STAFF_COLUMNS = [
    ("employee_code", "Unique employee code", True, None),
    ("full_name", "Full name", True, None),
    ("designation", "Designation", True, list(DESIGNATION_MAP)),
    ("work_location", "Office city", True, WORK_LOCATIONS),
    ("date_of_joining", "YYYY-MM-DD", False, None),
    ("status", "Active or Left", False, list(STAFF_STATUS_MAP)),
]

CLIENT_COLUMNS = [
    ("client_code", "Unique client code", True, None),
    ("name", "Client name", True, None),
    ("entity_type", "Stock-exchange listed?", True, list(ENTITY_TYPE_MAP)),
    ("nature", "Legal structure", True, list(NATURE_MAP)),
    ("engagement_type", "Primary type of engagement", False, list(ENGAGEMENT_TYPE_MAP)),
    ("status", "Active or Inactive", False, list(CLIENT_STATUS_MAP)),
    ("partner_responsible", "Partner responsible", True, PARTNERS),
    ("priority", "Client priority", False, list(PRIORITY_MAP)),
    ("mnc_status", "Multinational?", False, list(YES_NO_MAP)),
    ("group", "Group name (free text)", False, None),
    ("firm", "Practicing firm", False, FIRMS),
    ("nature_of_business", "Industry / nature of business", False, None),
]


def _add_dropdown(ws, col_index: int, allowed: list[str], max_row: int) -> None:
    formula = '"' + ",".join(allowed) + '"'
    dv = DataValidation(type="list", formula1=formula, allow_blank=True)
    ws.add_data_validation(dv)
    col_letter = ws.cell(row=1, column=col_index).column_letter
    dv.add(f"{col_letter}2:{col_letter}{max_row}")


def build() -> None:
    wb = Workbook()

    dd = wb.active
    dd.title = "Data Dictionary"
    dd.append(["Sheet", "Column", "Description", "Required", "Allowed values"])
    for sheet_name, columns in (("staff", STAFF_COLUMNS), ("clients", CLIENT_COLUMNS)):
        for col, desc, required, allowed in columns:
            dd.append([sheet_name, col, desc, "Yes" if required else "No", ", ".join(allowed) if allowed else ""])
    dd.freeze_panes = "A2"
    for i, width in enumerate([10, 22, 40, 10, 60], start=1):
        dd.column_dimensions[dd.cell(row=1, column=i).column_letter].width = width

    staff_ws = wb.create_sheet("staff")
    staff_ws.append([c[0] for c in STAFF_COLUMNS])
    designation_labels = list(DESIGNATION_MAP)
    for i in range(300):
        fn, ln = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        designation_label = "Partner" if i < 14 else random.choice([d for d in designation_labels if d != "Partner"])
        staff_ws.append([
            f"SAMP-E{i+1:04d}", f"{fn} {ln}", designation_label, random.choice(WORK_LOCATIONS),
            "2022-06-01", "Active",
        ])
    for idx, (col, _desc, _req, allowed) in enumerate(STAFF_COLUMNS, start=1):
        if allowed:
            _add_dropdown(staff_ws, idx, allowed, 301)
    staff_ws.freeze_panes = "A2"

    clients_ws = wb.create_sheet("clients")
    clients_ws.append([c[0] for c in CLIENT_COLUMNS])
    nature_labels = list(NATURE_MAP)
    engagement_labels = list(ENGAGEMENT_TYPE_MAP)
    for i in range(200):
        clients_ws.append([
            f"SAMP-CL{i+1:04d}", f"{random.choice(LAST_NAMES)} {random.choice(['Industries', 'Enterprises', 'Ltd'])}",
            random.choice(list(ENTITY_TYPE_MAP)), random.choice(nature_labels), random.choice(engagement_labels),
            "Active", random.choice(PARTNERS), random.choice(list(PRIORITY_MAP)),
            "Yes" if i % 13 == 0 else "No", "", random.choice(FIRMS),
            random.choice(["BFSI", "Manufacturing", "IT", "Pharma"]),
        ])
    for idx, (col, _desc, _req, allowed) in enumerate(CLIENT_COLUMNS, start=1):
        if allowed:
            _add_dropdown(clients_ws, idx, allowed, 201)
    clients_ws.freeze_panes = "A2"

    wb.save(OUT_PATH)
    print(f"Wrote {OUT_PATH} (300 staff rows, 200 client rows)")


if __name__ == "__main__":
    build()
