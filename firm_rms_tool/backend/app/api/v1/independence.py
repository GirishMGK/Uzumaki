"""Independence declarations CRUD + review workflow (§3.12, §9.4).

A declaration is created by/for a staff member against one client (self-
declaration in spirit — no `/me` endpoint exists yet, so any of the write
roles below can record one on someone's behalf). `is_conflicted` starts
false and is only ever flipped by an explicit review action so there's
always a named reviewer of record — the conflict engine's R5 and R24 read
`is_conflicted` / `held_employment_last_2yrs` directly off this table.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.core.deps import get_client_ip, get_current_user, get_db, require_roles
from app.core.soft_delete import reject_hard_delete
from app.models.allocation import IndependenceDeclaration
from app.models.enums import AuditAction, UserRole
from app.models.user import User
from app.schemas.independence import (
    IndependenceDeclarationCreate,
    IndependenceDeclarationRead,
    IndependenceDeclarationReview,
)

router = APIRouter()

WRITE_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER, UserRole.MANAGER, UserRole.HR)
REVIEW_ROLES = (UserRole.ADMIN, UserRole.PARTNER, UserRole.HR)


@router.get("", response_model=list[IndependenceDeclarationRead])
def list_declarations(
    staff_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    declaration_fy: str | None = None,
    is_conflicted: bool | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[IndependenceDeclarationRead]:
    stmt = select(IndependenceDeclaration).where(IndependenceDeclaration.is_active == True)  # noqa: E712
    if staff_id:
        stmt = stmt.where(IndependenceDeclaration.staff_id == staff_id)
    if client_id:
        stmt = stmt.where(IndependenceDeclaration.client_id == client_id)
    if declaration_fy:
        stmt = stmt.where(IndependenceDeclaration.declaration_fy == declaration_fy)
    if is_conflicted is not None:
        stmt = stmt.where(IndependenceDeclaration.is_conflicted == is_conflicted)
    return list(db.exec(stmt).all())


@router.get("/{declaration_id}", response_model=IndependenceDeclarationRead)
def get_declaration(declaration_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> IndependenceDeclaration:
    row = db.get(IndependenceDeclaration, declaration_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Independence declaration not found")
    return row


@router.post("", response_model=IndependenceDeclarationRead, status_code=status.HTTP_201_CREATED)
def create_declaration(
    payload: IndependenceDeclarationCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> IndependenceDeclaration:
    row = IndependenceDeclaration(
        **payload.model_dump(),
        declared_on=datetime.now(timezone.utc).date().isoformat(),
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.flush()
    write_audit_log(
        db, entity_type="independence_declarations", entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
        before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/{declaration_id}/review", response_model=IndependenceDeclarationRead)
def review_declaration(
    declaration_id: uuid.UUID,
    payload: IndependenceDeclarationReview,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*REVIEW_ROLES)),
) -> IndependenceDeclaration:
    row = db.get(IndependenceDeclaration, declaration_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Independence declaration not found")
    before = row.model_copy()
    row.is_conflicted = payload.is_conflicted
    if payload.notes:
        row.other_threat_description = ((row.other_threat_description or "") + f"\n[review by {user.email}]: {payload.notes}").strip()
    row.reviewed_by = user.id
    row.updated_by = user.id
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    write_audit_log(
        db, entity_type="independence_declarations", entity_id=row.id, action=AuditAction.APPROVE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{declaration_id}")
def cancel_declaration(
    declaration_id: uuid.UUID,
    request: Request,
    hard: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    reject_hard_delete(hard)
    row = db.get(IndependenceDeclaration, declaration_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Independence declaration not found")
    before = row.model_copy()
    row.is_active = False
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="independence_declarations", entity_id=row.id, action=AuditAction.DELETE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"status": "cancelled", "id": str(declaration_id)}
