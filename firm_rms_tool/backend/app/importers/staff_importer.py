from app.importers.base import ImportResult, required, run_validators, one_of, numeric
from app.models.enums import Designation, EmploymentStatus, StaffCategory
from app.models.staff import Staff

REQUIRED_COLUMNS = ["employee_code", "full_name", "staff_category", "designation", "grade_rank"]

VALIDATORS = [
    required("employee_code"),
    required("full_name"),
    one_of("staff_category", {e.value for e in StaffCategory}),
    one_of("designation", {e.value for e in Designation}),
    numeric("grade_rank", required_field=True),
    one_of("employment_status", {e.value for e in EmploymentStatus}, required_field=False),
]


def validate_staff_rows(rows: list[dict]) -> ImportResult:
    return run_validators(rows, VALIDATORS)


def row_to_staff_kwargs(row: dict) -> dict:
    return dict(
        employee_code=row["employee_code"].strip(),
        full_name=row["full_name"].strip(),
        short_name=row.get("short_name") or None,
        official_email=row.get("official_email") or None,
        mobile=row.get("mobile") or None,
        staff_category=row["staff_category"].strip(),
        designation=row["designation"].strip(),
        grade_rank=int(float(row["grade_rank"])),
        employment_status=(row.get("employment_status") or EmploymentStatus.ACTIVE.value).strip(),
        date_of_joining=row.get("date_of_joining") or None,
        home_city=row.get("home_city") or None,
    )
