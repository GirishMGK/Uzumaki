"""Acceptance test T11 (§14): bad rows -> row-level error report, 0 committed in all-or-nothing mode."""
import io

import openpyxl

from app.models.enums import UserRole
from tests.conftest import auth_headers, make_user


def _build_workbook(total_rows: int, bad_rows: int) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["employee_code", "full_name", "staff_category", "designation", "grade_rank"])
    for i in range(total_rows):
        if i < bad_rows:
            # invalid staff_category enum value
            ws.append([f"E{i}", f"Person {i}", "NOT_A_CATEGORY", "ASSOCIATE", "8"])
        else:
            ws.append([f"E{i}", f"Person {i}", "EMPLOYEE_CA", "ASSOCIATE", "8"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_t11_import_error_report_and_all_or_nothing(client, session):
    make_user(session, UserRole.ADMIN, email="importer@x.com")
    headers = auth_headers(client, "importer@x.com")

    workbook_bytes = _build_workbook(total_rows=50, bad_rows=5)

    validate_resp = client.post(
        "/api/v1/admin/import/staff/validate",
        headers=headers,
        files={"file": ("staff.xlsx", workbook_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert validate_resp.status_code == 200
    body = validate_resp.json()
    assert body["total_rows"] == 50
    assert body["error_count"] == 5
    assert body["valid_count"] == 45

    commit_resp = client.post(
        "/api/v1/admin/import/staff/commit",
        headers=headers,
        files={"file": ("staff.xlsx", workbook_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert commit_resp.status_code == 200
    commit_body = commit_resp.json()
    assert commit_body["committed"] == 0  # all-or-nothing: errors present -> nothing committed
    assert commit_body["error_count"] == 5

    list_resp = client.get("/api/v1/staff", headers=headers)
    assert list_resp.json() == []

    # commit_valid_only=true commits the 45 clean rows.
    commit_partial = client.post(
        "/api/v1/admin/import/staff/commit?commit_valid_only=true",
        headers=headers,
        files={"file": ("staff.xlsx", workbook_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert commit_partial.json()["committed"] == 45
