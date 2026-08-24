"""Resource request CRUD + fulfilment workflow (§3.13, §9.1).

`status` moves OPEN -> PARTIALLY_FILLED -> FILLED as allocations are linked
via `/fulfil`, purely a function of `len(fulfilment_allocation_ids)` vs.
`headcount` — there's no separate "mark filled" action so the status can
never drift from what's actually been booked.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.core.deps import get_client_ip, get_current_user, get_db, require_roles
from app.core.soft_delete import reject_hard_delete
from app.models.allocation import Allocation, ResourceRequest
from app.models.enums import AuditAction, ResourceRequestStatus, UserRole
from app.models.user import User
from app.schemas.resource_request import (
    ResourceRequestCreate,
    ResourceRequestFulfil,
    ResourceRequestRead,
)

router = APIRouter()

WRITE_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER, UserRole.MANAGER)


def _status_for(request_row: ResourceRequest) -> str:
    filled = len(request_row.fulfilment_allocation_ids or [])
    if filled <= 0:
        return ResourceRequestStatus.OPEN.value
    if filled >= request_row.headcount:
        return ResourceRequestStatus.FILLED.value
    return ResourceRequestStatus.PARTIALLY_FILLED.value


@router.get("", response_model=list[ResourceRequestRead])
def list_resource_requests(
    engagement_id: uuid.UUID | None = None,
    status_: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ResourceRequestRead]:
    stmt = select(ResourceRequest).where(ResourceRequest.is_active == True)  # noqa: E712
    if engagement_id:
        stmt = stmt.where(ResourceRequest.engagement_id == engagement_id)
    if status_:
        stmt = stmt.where(ResourceRequest.status == status_)
    return list(db.exec(stmt).all())


@router.get("/{request_id}", response_model=ResourceRequestRead)
def get_resource_request(request_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ResourceRequest:
    row = db.get(ResourceRequest, request_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Resource request not found")
    return row


@router.post("", response_model=ResourceRequestRead, status_code=status.HTTP_201_CREATED)
def create_resource_request(
    payload: ResourceRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> ResourceRequest:
    row = ResourceRequest(
        **payload.model_dump(),
        requested_by=user.id,
        requested_on=datetime.now(timezone.utc).date().isoformat(),
        status=ResourceRequestStatus.OPEN.value,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.flush()
    write_audit_log(
        db, entity_type="resource_requests", entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
        before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/{request_id}/fulfil", response_model=ResourceRequestRead)
def fulfil_resource_request(
    request_id: uuid.UUID,
    payload: ResourceRequestFulfil,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> ResourceRequest:
    row = db.get(ResourceRequest, request_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Resource request not found")
    if row.status == ResourceRequestStatus.REJECTED.value:
        raise HTTPException(status_code=422, detail="Cannot fulfil a rejected resource request")
    alloc = db.get(Allocation, payload.allocation_id)
    if alloc is None or not alloc.is_active:
        raise HTTPException(status_code=404, detail="Allocation not found")
    if alloc.engagement_id != row.engagement_id:
        raise HTTPException(status_code=422, detail="Allocation is for a different engagement than this request")
    before = row.model_copy()
    existing_ids = list(row.fulfilment_allocation_ids or [])
    if str(alloc.id) not in existing_ids:
        existing_ids.append(str(alloc.id))
    row.fulfilment_allocation_ids = existing_ids
    row.status = _status_for(row)
    row.updated_by = user.id
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    write_audit_log(
        db, entity_type="resource_requests", entity_id=row.id, action=AuditAction.UPDATE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/{request_id}/reject", response_model=ResourceRequestRead)
def reject_resource_request(
    request_id: uuid.UUID,
    request: Request,
    reason: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> ResourceRequest:
    row = db.get(ResourceRequest, request_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Resource request not found")
    before = row.model_copy()
    row.status = ResourceRequestStatus.REJECTED.value
    row.updated_by = user.id
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    write_audit_log(
        db, entity_type="resource_requests", entity_id=row.id, action=AuditAction.UPDATE, actor_id=user.id,
        before=before, after={**row.model_dump(mode="json"), "reject_reason": reason},
        ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{request_id}")
def cancel_resource_request(
    request_id: uuid.UUID,
    request: Request,
    hard: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    reject_hard_delete(hard)
    row = db.get(ResourceRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Resource request not found")
    before = row.model_copy()
    row.is_active = False
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="resource_requests", entity_id=row.id, action=AuditAction.DELETE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"status": "cancelled", "id": str(request_id)}
