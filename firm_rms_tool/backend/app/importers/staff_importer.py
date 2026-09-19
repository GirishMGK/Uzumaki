import uuid

from sqlmodel import Session

from app.importers.base import ImportResult, one_of_label, required, run_validators
from app.importers.friendly_values import DESIGNATION_MAP, STAFF_STATUS_MAP, WORK_LOCATIONS, lookup
from app.importers.resolvers import resolve_office_id, today_iso

VALIDATORS = [
    required("employee_code"),
    required("full_name"),
    one_of_label("designation", DESIGNATION_MAP.keys()),
    one_of_label("work_location", WORK_LOCATIONS),
    one_of_label("status", STAFF_STATUS_MAP.keys(), required_field=False),
]


def validate_staff_rows(rows: list[dict]) -> ImportResult:
    return run_validators(rows, VALIDATORS)


def row_to_staff_kwargs(row: dict, db: Session, actor_id: uuid.UUID) -> dict:
    """Turns one validated row into Staff(**kwargs). Needs `db` (and the
    importing user's id, for created_by/updated_by) because Work location
    resolves to an Office record — created on first use if it doesn't
    exist yet, same as the Add-one form's behaviour.
    """
    designation, staff_category, grade_rank = DESIGNATION_MAP[
        next(label for label in DESIGNATION_MAP if label.lower() == row["designation"].strip().lower())
    ]
    office_id = resolve_office_id(db, row["work_location"].strip(), actor_id)
    status = lookup(STAFF_STATUS_MAP, row.get("status") or "Active") or "ACTIVE"
    return dict(
        employee_code=row["employee_code"].strip(),
        full_name=row["full_name"].strip(),
        staff_category=staff_category,
        designation=designation,
        grade_rank=grade_rank,
        base_office_id=office_id,
        current_office_id=office_id,
        employment_status=status,
        date_of_joining=row.get("date_of_joining") or None,
        date_of_exit=today_iso() if status == "EXITED" else None,
    )
