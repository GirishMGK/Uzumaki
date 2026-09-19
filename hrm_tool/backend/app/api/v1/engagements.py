import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.core.deps import can_see_financials, get_client_ip, get_current_user, get_db, require_roles
from app.core.soft_delete import reject_hard_delete
from app.models.engagement import Engagement
from app.models.enums import AuditAction, UserRole
from app.models.user import User
from app.schemas.engagement import (
    EngagementCreate,
    EngagementRead,
    EngagementRollForwardRequest,
    EngagementRollForwardResponse,
)
from app.services.roll_forward import roll_forward_engagement

router = APIRouter()

WRITE_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER)


@router.get("", response_model=list[EngagementRead])
def list_engagements(
    skip: int = 0,
    limit: int = 100,
    client_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    financial_year: str | None = None,
    status_: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[EngagementRead]:
    stmt = select(Engagement).where(Engagement.is_active == True)  # noqa: E712
    if client_id:
        stmt = stmt.where(Engagement.client_id == client_id)
    if department_id:
        stmt = stmt.where(Engagement.department_id == department_id)
    if financial_year:
        stmt = stmt.where(Engagement.financial_year == financial_year)
    if status_:
        stmt = stmt.where(Engagement.status == status_)
    stmt = stmt.offset(skip).limit(limit)
    rows = db.exec(stmt).all()
    mask = not can_see_financials(user)
    return [EngagementRead.from_orm_masked(r, mask_financials=mask) for r in rows]


@router.get("/{engagement_id}", response_model=EngagementRead)
def get_engagement(
    engagement_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> EngagementRead:
    row = db.get(Engagement, engagement_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Engagement not found")
    return EngagementRead.from_orm_masked(row, mask_financials=not can_see_financials(user))


@router.post("", response_model=EngagementRead, status_code=status.HTTP_201_CREATED)
def create_engagement(
    payload: EngagementCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> EngagementRead:
    existing = db.exec(select(Engagement).where(Engagement.engagement_code == payload.engagement_code)).first()
    if existing:
        raise HTTPException(status_code=409, detail="engagement_code already exists")
    row = Engagement(**payload.model_dump(), created_by=user.id, updated_by=user.id)
    db.add(row)
    db.flush()
    write_audit_log(
        db, entity_type="engagements", entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
        before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return EngagementRead.from_orm_masked(row, mask_financials=not can_see_financials(user))


@router.delete("/{engagement_id}")
def delete_engagement(
    engagement_id: uuid.UUID,
    request: Request,
    hard: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN)),
) -> dict:
    reject_hard_delete(hard)
    row = db.get(Engagement, engagement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Engagement not found")
    before = row.model_copy()
    row.is_active = False
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="engagements", entity_id=row.id, action=AuditAction.DELETE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"status": "soft_deleted", "id": str(engagement_id)}


@router.post("/{engagement_id}/roll-forward", response_model=EngagementRollForwardResponse, status_code=status.HTTP_201_CREATED)
def roll_forward(
    engagement_id: uuid.UUID,
    payload: EngagementRollForwardRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> EngagementRollForwardResponse:
    source = db.get(Engagement, engagement_id)
    if source is None or not source.is_active:
        raise HTTPException(status_code=404, detail="Engagement not found")
    existing = db.exec(select(Engagement).where(Engagement.engagement_code == payload.new_engagement_code)).first()
    if existing:
        raise HTTPException(status_code=409, detail="new_engagement_code already exists")

    result = roll_forward_engagement(
        db, source,
        new_engagement_code=payload.new_engagement_code, new_financial_year=payload.new_financial_year,
        date_shift_years=payload.date_shift_years, copy_team=payload.copy_team,
        actor_id=user.id, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return EngagementRollForwardResponse(
        new_engagement=EngagementRead.from_orm_masked(result.new_engagement, mask_financials=not can_see_financials(user)),
        copied=result.copied, skipped=result.skipped,
    )
