import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.core.deps import get_client_ip, get_current_user, get_db, require_roles
from app.core.soft_delete import reject_hard_delete
from app.models.allocation import Allocation, NonAvailability
from app.models.enums import AllocationStatus, AuditAction, UserRole
from app.models.user import User
from app.schemas.non_availability import NonAvailabilityCreate, NonAvailabilityRead
from app.services.capacity_materializer import recompute_range

router = APIRouter()

APPROVER_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER, UserRole.MANAGER)


def _invalidate_capacity(db: Session, staff_id: uuid.UUID, date_from: str, date_to: str) -> None:
    """§5: leave only affects net_capacity_hrs once APPROVED (or un-approved)."""
    d_from = datetime.strptime(date_from, "%Y-%m-%d").date() - timedelta(days=1)
    d_to = datetime.strptime(date_to, "%Y-%m-%d").date() + timedelta(days=1)
    recompute_range(db, d_from, d_to, staff_ids=[staff_id])


@router.get("", response_model=list[NonAvailabilityRead])
def list_non_availability(
    staff_id: uuid.UUID | None = None,
    status_: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[NonAvailabilityRead]:
    stmt = select(NonAvailability).where(NonAvailability.is_active == True)  # noqa: E712
    if staff_id:
        stmt = stmt.where(NonAvailability.staff_id == staff_id)
    if status_:
        stmt = stmt.where(NonAvailability.status == status_)
    return list(db.exec(stmt).all())


@router.post("", response_model=NonAvailabilityRead, status_code=status.HTTP_201_CREATED)
def apply_leave(
    payload: NonAvailabilityCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NonAvailability:
    row = NonAvailability(**payload.model_dump(), status="APPLIED", created_by=user.id, updated_by=user.id)
    db.add(row)
    db.flush()
    write_audit_log(
        db, entity_type="non_availability", entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
        before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


def _conflicting_confirmed_allocations(db: Session, staff_id: uuid.UUID, date_from: str, date_to: str) -> list[Allocation]:
    stmt = (
        select(Allocation)
        .where(Allocation.staff_id == staff_id)
        .where(Allocation.is_active == True)  # noqa: E712
        .where(Allocation.status.in_([AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS]))  # type: ignore[attr-defined]
        .where(Allocation.date_from <= date_to)
        .where(Allocation.date_to >= date_from)
    )
    return list(db.exec(stmt).all())


@router.get("/{leave_id}/conflicts")
def leave_conflicts(leave_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    leave = db.get(NonAvailability, leave_id)
    if leave is None:
        raise HTTPException(status_code=404, detail="Leave record not found")
    conflicts = _conflicting_confirmed_allocations(db, leave.staff_id, leave.date_from, leave.date_to)
    return {"leave_id": str(leave_id), "conflict_count": len(conflicts), "allocation_ids": [str(c.id) for c in conflicts]}


@router.post("/{leave_id}/approve", response_model=NonAvailabilityRead)
def approve_leave(
    leave_id: uuid.UUID,
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*APPROVER_ROLES)),
) -> NonAvailability:
    row = db.get(NonAvailability, leave_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Leave record not found")
    conflicts = _conflicting_confirmed_allocations(db, row.staff_id, row.date_from, row.date_to)
    if conflicts and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Leave conflicts with confirmed allocations; resolve or reassign before approving.",
                "allocation_ids": [str(c.id) for c in conflicts],
            },
        )
    before = row.model_copy()
    row.status = "APPROVED"
    row.approved_by = user.id
    row.updated_by = user.id
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    write_audit_log(
        db, entity_type="non_availability", entity_id=row.id, action=AuditAction.APPROVE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    _invalidate_capacity(db, row.staff_id, row.date_from, row.date_to)
    return row


@router.post("/{leave_id}/reject", response_model=NonAvailabilityRead)
def reject_leave(
    leave_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*APPROVER_ROLES)),
) -> NonAvailability:
    row = db.get(NonAvailability, leave_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Leave record not found")
    before = row.model_copy()
    row.status = "REJECTED"
    row.approved_by = user.id
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="non_availability", entity_id=row.id, action=AuditAction.UPDATE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    if before.status == "APPROVED":  # was affecting capacity; no longer is
        _invalidate_capacity(db, row.staff_id, row.date_from, row.date_to)
    return row


@router.delete("/{leave_id}")
def cancel_leave(
    leave_id: uuid.UUID,
    request: Request,
    hard: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    reject_hard_delete(hard)
    row = db.get(NonAvailability, leave_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Leave record not found")
    before = row.model_copy()
    row.status = "CANCELLED"
    row.is_active = False
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="non_availability", entity_id=row.id, action=AuditAction.DELETE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    if before.status == "APPROVED":
        _invalidate_capacity(db, row.staff_id, row.date_from, row.date_to)
    return {"status": "cancelled", "id": str(leave_id)}
