"""Timesheet CRUD + submission/approval workflow (§3.11, §9).

DRAFT -> SUBMITTED -> APPROVED/REJECTED. Only APPROVED timesheets count as
actuals (see `app/services/actuals.py`) — the same "evidence, not a guess"
posture as the rest of this build: nothing feeds margin/utilisation
numbers until someone with authority has signed off on it.

RBAC is finer-grained than a flat role list: STAFF/MANAGER can only
log/edit/submit their own time (matched against `User.staff_id`);
ADMIN/RESOURCE_MANAGER/PARTNER/HR can act on anyone's behalf. This is the
service-level check (layer 2 of the 3-layer RBAC model, §2) sitting under
the route-level `get_current_user` dependency.
"""
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.core.deps import get_client_ip, get_current_user, get_db
from app.core.soft_delete import reject_hard_delete
from app.models.allocation import Timesheet
from app.models.enums import AuditAction, TimesheetStatus, UserRole
from app.models.user import User
from app.schemas.timesheet import TimesheetCreate, TimesheetRead, TimesheetUpdate

router = APIRouter()

PRIVILEGED_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER, UserRole.HR)
APPROVER_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER, UserRole.MANAGER)


def _assert_can_act_on(user: User, staff_id: uuid.UUID) -> None:
    if user.role == UserRole.VIEWER:
        raise HTTPException(status_code=403, detail="Viewers cannot log or edit timesheets")
    if user.role in PRIVILEGED_ROLES:
        return
    if user.staff_id is None or user.staff_id != staff_id:
        raise HTTPException(status_code=403, detail="You can only log or edit your own timesheet entries")


@router.get("", response_model=list[TimesheetRead])
def list_timesheets(
    staff_id: uuid.UUID | None = None,
    engagement_id: uuid.UUID | None = None,
    status_: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[TimesheetRead]:
    stmt = select(Timesheet).where(Timesheet.is_active == True)  # noqa: E712
    if staff_id:
        stmt = stmt.where(Timesheet.staff_id == staff_id)
    elif user.role not in PRIVILEGED_ROLES and user.role != UserRole.VIEWER and user.staff_id:
        # Self-service roles browsing without an explicit staff_id see only their own rows.
        stmt = stmt.where(Timesheet.staff_id == user.staff_id)
    if engagement_id:
        stmt = stmt.where(Timesheet.engagement_id == engagement_id)
    if status_:
        stmt = stmt.where(Timesheet.status == status_)
    if date_from:
        stmt = stmt.where(Timesheet.work_date >= date_from.isoformat())
    if date_to:
        stmt = stmt.where(Timesheet.work_date <= date_to.isoformat())
    return list(db.exec(stmt).all())


@router.get("/{timesheet_id}", response_model=TimesheetRead)
def get_timesheet(timesheet_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Timesheet:
    row = db.get(Timesheet, timesheet_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    return row


@router.post("", response_model=TimesheetRead, status_code=status.HTTP_201_CREATED)
def create_timesheet(
    payload: TimesheetCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Timesheet:
    _assert_can_act_on(user, payload.staff_id)
    if payload.hours <= 0:
        raise HTTPException(status_code=422, detail="hours must be positive")
    row = Timesheet(
        **payload.model_dump(), status=TimesheetStatus.DRAFT.value, created_by=user.id, updated_by=user.id,
    )
    db.add(row)
    db.flush()
    write_audit_log(
        db, entity_type="timesheets", entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
        before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{timesheet_id}", response_model=TimesheetRead)
def update_timesheet(
    timesheet_id: uuid.UUID,
    payload: TimesheetUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Timesheet:
    row = db.get(Timesheet, timesheet_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    _assert_can_act_on(user, row.staff_id)
    if row.status != TimesheetStatus.DRAFT.value:
        raise HTTPException(status_code=422, detail="Only DRAFT timesheets can be edited")
    before = row.model_copy()
    updates = payload.model_dump(exclude_unset=True)
    if "hours" in updates and updates["hours"] is not None and updates["hours"] <= 0:
        raise HTTPException(status_code=422, detail="hours must be positive")
    for field, value in updates.items():
        setattr(row, field, value)
    row.updated_by = user.id
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    write_audit_log(
        db, entity_type="timesheets", entity_id=row.id, action=AuditAction.UPDATE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/{timesheet_id}/submit", response_model=TimesheetRead)
def submit_timesheet(
    timesheet_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Timesheet:
    row = db.get(Timesheet, timesheet_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    _assert_can_act_on(user, row.staff_id)
    if row.status != TimesheetStatus.DRAFT.value:
        raise HTTPException(status_code=422, detail="Only DRAFT timesheets can be submitted")
    before = row.model_copy()
    row.status = TimesheetStatus.SUBMITTED.value
    row.updated_by = user.id
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    write_audit_log(
        db, entity_type="timesheets", entity_id=row.id, action=AuditAction.UPDATE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/{timesheet_id}/approve", response_model=TimesheetRead)
def approve_timesheet(
    timesheet_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Timesheet:
    if user.role not in APPROVER_ROLES:
        raise HTTPException(status_code=403, detail=f"Role {user.role} is not permitted to approve timesheets")
    row = db.get(Timesheet, timesheet_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    if row.status != TimesheetStatus.SUBMITTED.value:
        raise HTTPException(status_code=422, detail="Only SUBMITTED timesheets can be approved")
    before = row.model_copy()
    row.status = TimesheetStatus.APPROVED.value
    row.approved_by = user.id
    row.approved_on = datetime.now(timezone.utc).date().isoformat()
    row.updated_by = user.id
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    write_audit_log(
        db, entity_type="timesheets", entity_id=row.id, action=AuditAction.APPROVE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/{timesheet_id}/reject", response_model=TimesheetRead)
def reject_timesheet(
    timesheet_id: uuid.UUID,
    request: Request,
    reason: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Timesheet:
    if user.role not in APPROVER_ROLES:
        raise HTTPException(status_code=403, detail=f"Role {user.role} is not permitted to reject timesheets")
    row = db.get(Timesheet, timesheet_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    if row.status != TimesheetStatus.SUBMITTED.value:
        raise HTTPException(status_code=422, detail="Only SUBMITTED timesheets can be rejected")
    before = row.model_copy()
    row.status = TimesheetStatus.REJECTED.value
    row.approved_by = user.id
    row.updated_by = user.id
    row.updated_at = datetime.now(timezone.utc)
    if reason:
        row.narration = ((row.narration or "") + f"\n[rejected by {user.email}]: {reason}").strip()
    db.add(row)
    write_audit_log(
        db, entity_type="timesheets", entity_id=row.id, action=AuditAction.UPDATE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{timesheet_id}")
def cancel_timesheet(
    timesheet_id: uuid.UUID,
    request: Request,
    hard: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    reject_hard_delete(hard)
    row = db.get(Timesheet, timesheet_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    if row.status == TimesheetStatus.APPROVED.value and user.role not in PRIVILEGED_ROLES:
        raise HTTPException(status_code=403, detail="Only Admin/RM/Partner/HR can remove an approved timesheet")
    _assert_can_act_on(user, row.staff_id)
    before = row.model_copy()
    row.is_active = False
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="timesheets", entity_id=row.id, action=AuditAction.DELETE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"status": "cancelled", "id": str(timesheet_id)}
