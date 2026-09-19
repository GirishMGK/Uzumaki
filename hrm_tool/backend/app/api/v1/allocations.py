import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.core.deps import get_client_ip, get_current_user, get_db, require_roles
from app.core.soft_delete import reject_hard_delete
from app.models.allocation import Allocation
from app.models.enums import AuditAction, UserRole
from app.models.user import User
from app.schemas.allocation import (
    AllocationCreate,
    AllocationRead,
    AllocationUpdate,
    AllocationValidateRequest,
    AllocationValidateResponse,
    RuleViolationOut,
)
from app.models.client import Client
from app.models.engagement import Engagement
from app.models.staff import Staff
from app.services.capacity_materializer import recompute_range
from app.services.conflict_engine import AllocationCandidate, has_blocking, validate_allocation
from app.services.notifications import notify_allocation_cancelled, notify_allocation_confirmed

router = APIRouter()

APPROVER_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER)
WRITE_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER, UserRole.MANAGER)


def _invalidate_capacity(db: Session, staff_id: uuid.UUID, *date_strs: str) -> None:
    """§5: recompute capacity_daily synchronously for every allocation mutation.

    Spans the union of all given dates (old + new, on a move) padded by a
    day so a booking that moved off a date still clears that date's cache.
    """
    dates = [datetime.strptime(d, "%Y-%m-%d").date() for d in date_strs if d]
    if not dates:
        return
    recompute_range(db, min(dates) - timedelta(days=1), max(dates) + timedelta(days=1), staff_ids=[staff_id])


def _notify(db: Session, row: Allocation, *, cancelled: bool, reason: str | None = None) -> None:
    """§9: best-effort email on allocation confirm/cancel. Never raises — a
    down/unconfigured mail server must never affect the booking itself."""
    staff = db.get(Staff, row.staff_id)
    engagement = db.get(Engagement, row.engagement_id)
    client = db.get(Client, engagement.client_id) if engagement else None
    if staff is None or engagement is None:
        return
    to_email = staff.official_email or staff.personal_email
    args = (to_email, staff.full_name, engagement.engagement_code, client.name if client else "", row.date_from, row.date_to)
    if cancelled:
        notify_allocation_cancelled(*args, reason=reason)
    else:
        notify_allocation_confirmed(*args)


def _candidate_from(payload, exclude_id: uuid.UUID | None = None) -> AllocationCandidate:
    return AllocationCandidate(
        engagement_id=payload.engagement_id,
        staff_id=payload.staff_id,
        role_on_engagement=payload.role_on_engagement,
        date_from=payload.date_from,
        date_to=payload.date_to,
        allocation_pct=payload.allocation_pct,
        status=payload.status,
        exclude_allocation_id=exclude_id,
        office_id=getattr(payload, "office_id", None),
        work_location=getattr(payload, "work_location", None),
    )


@router.post("/validate", response_model=AllocationValidateResponse)
def validate_endpoint(
    payload: AllocationValidateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AllocationValidateResponse:
    cand = _candidate_from(payload, exclude_id=payload.exclude_allocation_id)
    violations = validate_allocation(db, cand)
    return AllocationValidateResponse(
        is_blocked=has_blocking(violations),
        violations=[RuleViolationOut(**v.__dict__) for v in violations],
    )


def _apply_overrides_or_raise(violations, overrides: list, actor_role: UserRole) -> list[dict]:
    """BLOCKs always fail. WARNs need a matching typed override reason."""
    blocking = [v for v in violations if v.severity == "BLOCK"]
    if blocking:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Blocking rule violations", "violations": [v.__dict__ for v in violations]},
        )
    override_by_code = {o.code: o.reason for o in overrides}
    recorded_overrides = []
    unresolved = []
    for v in violations:
        if v.severity != "WARN":
            continue
        if v.code in override_by_code:
            recorded_overrides.append({"code": v.code, "reason": override_by_code[v.code]})
        else:
            unresolved.append(v)
    if unresolved:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "WARN-level rule violations require a typed override reason",
                "violations": [v.__dict__ for v in unresolved],
            },
        )
    return recorded_overrides


@router.get("", response_model=list[AllocationRead])
def list_allocations(
    engagement_id: uuid.UUID | None = None,
    staff_id: uuid.UUID | None = None,
    status_: str | None = None,
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AllocationRead]:
    stmt = select(Allocation).where(Allocation.is_active == True)  # noqa: E712
    if engagement_id:
        stmt = stmt.where(Allocation.engagement_id == engagement_id)
    if staff_id:
        stmt = stmt.where(Allocation.staff_id == staff_id)
    if status_:
        stmt = stmt.where(Allocation.status == status_)
    stmt = stmt.offset(skip).limit(limit)
    return list(db.exec(stmt).all())


@router.get("/{allocation_id}", response_model=AllocationRead)
def get_allocation(allocation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Allocation:
    row = db.get(Allocation, allocation_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Allocation not found")
    return row


@router.post("", response_model=AllocationRead, status_code=status.HTTP_201_CREATED)
def create_allocation(
    payload: AllocationCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> Allocation:
    cand = _candidate_from(payload)
    violations = validate_allocation(db, cand)
    recorded_overrides = _apply_overrides_or_raise(violations, payload.overrides, user.role)

    data = payload.model_dump(exclude={"overrides"})
    row = Allocation(
        **data,
        override_flags=[{**o, "by": str(user.id), "on": datetime.now(timezone.utc).isoformat()} for o in recorded_overrides] or None,
        requested_by=user.id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.flush()
    write_audit_log(
        db, entity_type="allocations", entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
        before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    _invalidate_capacity(db, row.staff_id, row.date_from, row.date_to)
    return row


@router.patch("/{allocation_id}", response_model=AllocationRead)
def update_allocation(
    allocation_id: uuid.UUID,
    payload: AllocationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> Allocation:
    row = db.get(Allocation, allocation_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Allocation not found")
    before = row.model_copy()

    updates = payload.model_dump(exclude_unset=True, exclude={"overrides"})
    cand = AllocationCandidate(
        engagement_id=row.engagement_id,
        staff_id=row.staff_id,
        role_on_engagement=updates.get("role_on_engagement", row.role_on_engagement),
        date_from=updates.get("date_from", row.date_from),
        date_to=updates.get("date_to", row.date_to),
        allocation_pct=updates.get("allocation_pct", row.allocation_pct),
        status=updates.get("status", row.status),
        exclude_allocation_id=row.id,
        office_id=updates.get("office_id", row.office_id),
        work_location=updates.get("work_location", row.work_location),
    )
    violations = validate_allocation(db, cand)
    recorded_overrides = _apply_overrides_or_raise(violations, payload.overrides, user.role)

    for field, value in updates.items():
        setattr(row, field, value)
    if recorded_overrides:
        row.override_flags = (row.override_flags or []) + [
            {**o, "by": str(user.id), "on": datetime.now(timezone.utc).isoformat()} for o in recorded_overrides
        ]
    row.version += 1
    row.updated_by = user.id
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    write_audit_log(
        db, entity_type="allocations", entity_id=row.id, action=AuditAction.UPDATE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    _invalidate_capacity(db, row.staff_id, before.date_from, before.date_to, row.date_from, row.date_to)
    return row


@router.post("/{allocation_id}/approve", response_model=AllocationRead)
def approve_allocation(
    allocation_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*APPROVER_ROLES)),
) -> Allocation:
    row = db.get(Allocation, allocation_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Allocation not found")
    before = row.model_copy()
    row.status = "CONFIRMED"  # type: ignore[assignment]
    row.approved_by = user.id
    row.approved_on = datetime.now(timezone.utc).date().isoformat()
    row.version += 1
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="allocations", entity_id=row.id, action=AuditAction.APPROVE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    _invalidate_capacity(db, row.staff_id, row.date_from, row.date_to)
    _notify(db, row, cancelled=False)
    return row


@router.delete("/{allocation_id}")
def cancel_allocation(
    allocation_id: uuid.UUID,
    request: Request,
    hard: bool = False,
    reason: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    reject_hard_delete(hard)
    row = db.get(Allocation, allocation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Allocation not found")
    before = row.model_copy()
    row.status = "CANCELLED"  # type: ignore[assignment]
    row.cancellation_reason = reason
    row.is_active = False
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="allocations", entity_id=row.id, action=AuditAction.DELETE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    _invalidate_capacity(db, row.staff_id, row.date_from, row.date_to)
    _notify(db, row, cancelled=True, reason=reason)
    return {"status": "cancelled", "id": str(allocation_id)}
