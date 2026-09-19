"""Builds the blank .xlsx templates downloadable from the Masters page
itself ("Download template" in each import panel) — as opposed to
seed/sample_masters.xlsx, which is pre-filled demo data for trying the
tool out. These are just headers, one greyed-out example row, dropdown
validation, and ~500 blank rows ready for the firm's real list.

Column order and dropdown choices come from app.importers.friendly_values
— the same dicts the importers validate against — so a template filled
in exactly as labeled always imports cleanly.
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Font
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

BLANK_ROWS = 500

# (header, dropdown choices or None for free text, example value)
STAFF_COLUMNS = [
    ("employee_code", None, "EMP-101"),
    ("full_name", None, "Ravi Kumar"),
    ("designation", list(DESIGNATION_MAP), "Manager"),
    ("work_location", WORK_LOCATIONS, "Hyderabad"),
    ("date_of_joining", None, "2024-01-15"),
    ("status", list(STAFF_STATUS_MAP), "Active"),
]

CLIENT_COLUMNS = [
    ("client_code", None, "CL-101"),
    ("name", None, "Acme Pvt Ltd"),
    ("entity_type", list(ENTITY_TYPE_MAP), "Non-Listed"),
    ("nature", list(NATURE_MAP), "Private"),
    ("engagement_type", list(ENGAGEMENT_TYPE_MAP), "Statutory audit"),
    ("status", list(CLIENT_STATUS_MAP), "Active"),
    ("partner_responsible", PARTNERS, PARTNERS[0]),
    ("priority", list(PRIORITY_MAP), "Medium"),
    ("mnc_status", list(YES_NO_MAP), "No"),
    ("group", None, "Acme Group"),
    ("firm", FIRMS, FIRMS[0]),
    ("nature_of_business", None, "Textile manufacturing"),
]


def _add_dropdown(ws, col_index: int, choices: list[str], max_row: int) -> None:
    formula = '"' + ",".join(choices) + '"'
    dv = DataValidation(type="list", formula1=formula, allow_blank=True, showErrorMessage=True)
    dv.error = "Pick one of the dropdown options."
    ws.add_data_validation(dv)
    col_letter = ws.cell(row=1, column=col_index).column_letter
    dv.add(f"{col_letter}2:{col_letter}{max_row}")


def _build(sheet_name: str, columns: list[tuple[str, list[str] | None, str]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name

    header_font = Font(bold=True)
    for col_index, (header, _choices, _example) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_index, value=header)
        cell.font = header_font

    example_font = Font(italic=True, color="808080")
    for col_index, (_header, _choices, example) in enumerate(columns, start=1):
        cell = ws.cell(row=2, column=col_index, value=example)
        cell.font = example_font
    ws.cell(row=2, column=len(columns) + 1, value="← example row, delete before importing").font = example_font

    max_row = BLANK_ROWS + 2
    for col_index, (_header, choices, _example) in enumerate(columns, start=1):
        if choices:
            _add_dropdown(ws, col_index, choices, max_row)

    for col_index, (header, _choices, _example) in enumerate(columns, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col_index).column_letter].width = max(len(header) + 4, 16)

    ws.freeze_panes = "A3"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_staff_template() -> bytes:
    return _build("staff", STAFF_COLUMNS)


def build_clients_template() -> bytes:
    return _build("clients", CLIENT_COLUMNS)
