import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.core.deps import can_see_financials, get_client_ip, get_current_user, get_db, require_roles
from app.core.soft_delete import reject_hard_delete
from app.models.enums import AuditAction, UserRole
from app.models.staff import Staff
from app.models.user import User
from app.schemas.staff import StaffCreate, StaffRead, StaffUpdate

router = APIRouter()

WRITE_ROLES = (UserRole.ADMIN, UserRole.HR, UserRole.RESOURCE_MANAGER)


@router.get("", response_model=list[StaffRead])
def list_staff(
    skip: int = 0,
    limit: int = 100,
    office_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    staff_category: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[StaffRead]:
    stmt = select(Staff).where(Staff.is_active == True)  # noqa: E712
    if office_id:
        stmt = stmt.where(Staff.base_office_id == office_id)
    if department_id:
        stmt = stmt.where(Staff.primary_department_id == department_id)
    if staff_category:
        stmt = stmt.where(Staff.staff_category == staff_category)
    if q:
        stmt = stmt.where(Staff.full_name.contains(q))  # type: ignore[union-attr]
    stmt = stmt.offset(skip).limit(limit)
    rows = db.exec(stmt).all()
    mask = not can_see_financials(user)
    return [StaffRead.from_orm_masked(r, mask_financials=mask) for r in rows]


@router.get("/{staff_id}", response_model=StaffRead)
def get_staff(staff_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> StaffRead:
    row = db.get(Staff, staff_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Staff not found")
    return StaffRead.from_orm_masked(row, mask_financials=not can_see_financials(user))


@router.post("", response_model=StaffRead, status_code=status.HTTP_201_CREATED)
def create_staff(
    payload: StaffCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> StaffRead:
    existing = db.exec(select(Staff).where(Staff.employee_code == payload.employee_code)).first()
    if existing:
        raise HTTPException(status_code=409, detail="employee_code already exists")
    row = Staff(**payload.model_dump(), created_by=user.id, updated_by=user.id)
    db.add(row)
    db.flush()
    write_audit_log(
        db,
        entity_type="staff",
        entity_id=row.id,
        action=AuditAction.CREATE,
        actor_id=user.id,
        before=None,
        after=row,
        ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return StaffRead.from_orm_masked(row, mask_financials=not can_see_financials(user))


@router.patch("/{staff_id}", response_model=StaffRead)
def update_staff(
    staff_id: uuid.UUID,
    payload: StaffUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> StaffRead:
    row = db.get(Staff, staff_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Staff not found")
    before = row.model_copy()
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(row, field, value)
    row.updated_by = user.id
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    write_audit_log(
        db,
        entity_type="staff",
        entity_id=row.id,
        action=AuditAction.UPDATE,
        actor_id=user.id,
        before=before,
        after=row,
        ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return StaffRead.from_orm_masked(row, mask_financials=not can_see_financials(user))


@router.delete("/{staff_id}")
def delete_staff(
    staff_id: uuid.UUID,
    request: Request,
    hard: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN)),
) -> dict:
    """Soft delete only. Hard delete is never exposed — see T16."""
    reject_hard_delete(hard)
    row = db.get(Staff, staff_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Staff not found")
    before = row.model_copy()
    row.is_active = False
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db,
        entity_type="staff",
        entity_id=row.id,
        action=AuditAction.DELETE,
        actor_id=user.id,
        before=before,
        after=row,
        ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"status": "soft_deleted", "id": str(staff_id)}
