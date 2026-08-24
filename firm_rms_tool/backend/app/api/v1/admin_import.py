"""§10 bulk import: two-phase validate/commit with a row-level error report.

`POST .../validate` never writes to the DB. `POST .../commit` re-validates
(never trusts client-side state) and is all-or-nothing unless
`commit_valid_only=true` is passed, in which case only clean rows commit.
"""
import uuid
from datetime import datetime, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.core.deps import get_current_user, get_db, require_roles
from app.importers.base import build_error_workbook, read_workbook_rows
from app.importers.clients_importer import row_to_client_kwargs, validate_client_rows
from app.importers.staff_importer import row_to_staff_kwargs, validate_staff_rows
from app.models.client import Client
from app.models.enums import AuditAction, UserRole
from app.models.staff import Staff
from app.models.user import User

router = APIRouter()

IMPORT_ROLES = (UserRole.ADMIN, UserRole.HR, UserRole.RESOURCE_MANAGER)


def _summary(result, entity: str) -> dict:
    return {
        "entity": entity,
        "total_rows": result.total_rows,
        "valid_count": len(result.valid_rows),
        "error_count": len(result.errors),
        "errors": [e.__dict__ for e in result.errors[:2000]],
    }


@router.post("/staff/validate")
async def validate_staff_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*IMPORT_ROLES)),
) -> dict:
    rows = read_workbook_rows(await file.read())
    result = validate_staff_rows(rows)
    return _summary(result, "staff")


@router.post("/staff/validate/error-workbook")
async def validate_staff_import_workbook(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*IMPORT_ROLES)),
) -> StreamingResponse:
    rows = read_workbook_rows(await file.read())
    result = validate_staff_rows(rows)
    xlsx_bytes = build_error_workbook(result.errors)
    return StreamingResponse(
        BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=staff_import_errors.xlsx"},
    )


@router.post("/staff/commit")
async def commit_staff_import(
    file: UploadFile = File(...),
    commit_valid_only: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*IMPORT_ROLES)),
) -> dict:
    rows = read_workbook_rows(await file.read())
    result = validate_staff_rows(rows)

    if result.errors and not commit_valid_only:
        return {**_summary(result, "staff"), "committed": 0, "message": "All-or-nothing import: 0 rows committed due to errors."}

    committed = 0
    codes_seen: set[str] = set()
    for row in result.valid_rows:
        kwargs = row_to_staff_kwargs(row)
        if kwargs["employee_code"] in codes_seen:
            continue
        codes_seen.add(kwargs["employee_code"])
        existing = db.exec(select(Staff).where(Staff.employee_code == kwargs["employee_code"])).first()
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            existing.updated_by = user.id
            existing.updated_at = datetime.now(timezone.utc)
            db.add(existing)
        else:
            new_row = Staff(**kwargs, created_by=user.id, updated_by=user.id)
            db.add(new_row)
        committed += 1
    write_audit_log(
        db, entity_type="staff", entity_id=uuid.uuid4(), action=AuditAction.CREATE, actor_id=user.id,
        before=None, after={"bulk_import": True, "committed": committed, "errors": len(result.errors)},
    )
    db.commit()
    return {**_summary(result, "staff"), "committed": committed}


@router.post("/clients/validate")
async def validate_clients_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*IMPORT_ROLES)),
) -> dict:
    rows = read_workbook_rows(await file.read())
    result = validate_client_rows(rows)
    return _summary(result, "clients")


@router.post("/clients/commit")
async def commit_clients_import(
    file: UploadFile = File(...),
    commit_valid_only: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*IMPORT_ROLES)),
) -> dict:
    rows = read_workbook_rows(await file.read())
    result = validate_client_rows(rows)

    if result.errors and not commit_valid_only:
        return {**_summary(result, "clients"), "committed": 0, "message": "All-or-nothing import: 0 rows committed due to errors."}

    committed = 0
    codes_seen: set[str] = set()
    for row in result.valid_rows:
        kwargs = row_to_client_kwargs(row)
        if kwargs["client_code"] in codes_seen:
            continue
        codes_seen.add(kwargs["client_code"])
        existing = db.exec(select(Client).where(Client.client_code == kwargs["client_code"])).first()
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            existing.updated_by = user.id
            existing.updated_at = datetime.now(timezone.utc)
            db.add(existing)
        else:
            new_row = Client(**kwargs, created_by=user.id, updated_by=user.id)
            db.add(new_row)
        committed += 1
    write_audit_log(
        db, entity_type="clients", entity_id=uuid.uuid4(), action=AuditAction.CREATE, actor_id=user.id,
        before=None, after={"bulk_import": True, "committed": committed, "errors": len(result.errors)},
    )
    db.commit()
    return {**_summary(result, "clients"), "committed": committed}
