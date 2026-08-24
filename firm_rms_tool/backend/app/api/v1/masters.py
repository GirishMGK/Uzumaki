"""Small reference-data routers: offices, departments, skills, client_groups.

These are simple lookup tables (no financial masking, no complex rules), so
a single factory builds list/get/create/soft-delete for each rather than
hand-rolling four near-identical routers.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlmodel import Session, SQLModel, select

from app.core.audit import write_audit_log
from app.core.deps import get_client_ip, get_current_user, get_db, require_roles
from app.core.soft_delete import reject_hard_delete
from app.models.client import ClientGroup
from app.models.enums import AuditAction, UserRole
from app.models.reference import Department, Office, Skill
from app.models.user import User
from app.schemas.reference import (
    ClientGroupCreate,
    ClientGroupRead,
    DepartmentCreate,
    DepartmentRead,
    OfficeCreate,
    OfficeRead,
    SkillCreate,
    SkillRead,
)

WRITE_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.HR)


def build_master_router(
    *, model: type[SQLModel], create_schema: type[BaseModel], read_schema: type[BaseModel], entity_type: str
) -> APIRouter:
    router = APIRouter()

    @router.get("", response_model=list[read_schema])  # type: ignore[valid-type]
    def list_rows(skip: int = 0, limit: int = 500, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        stmt = select(model).where(model.is_active == True).offset(skip).limit(limit)  # noqa: E712
        return list(db.exec(stmt).all())

    @router.get("/{row_id}", response_model=read_schema)  # type: ignore[valid-type]
    def get_row(row_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        row = db.get(model, row_id)
        if row is None or not row.is_active:
            raise HTTPException(status_code=404, detail=f"{entity_type} not found")
        return row

    @router.post("", response_model=read_schema, status_code=status.HTTP_201_CREATED)  # type: ignore[valid-type]
    def create_row(
        payload: create_schema,  # type: ignore[valid-type]
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(require_roles(*WRITE_ROLES)),
    ):
        row = model(**payload.model_dump(), created_by=user.id, updated_by=user.id)
        db.add(row)
        db.flush()
        write_audit_log(
            db, entity_type=entity_type, entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
            before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(row)
        return row

    @router.delete("/{row_id}")
    def delete_row(
        row_id: uuid.UUID,
        request: Request,
        hard: bool = False,
        db: Session = Depends(get_db),
        user: User = Depends(require_roles(UserRole.ADMIN)),
    ) -> dict:
        reject_hard_delete(hard)
        row = db.get(model, row_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"{entity_type} not found")
        before = row.model_copy()
        row.is_active = False
        row.deleted_at = datetime.now(timezone.utc)
        row.updated_by = user.id
        db.add(row)
        write_audit_log(
            db, entity_type=entity_type, entity_id=row.id, action=AuditAction.DELETE, actor_id=user.id,
            before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        return {"status": "soft_deleted", "id": str(row_id)}

    return router


offices_router = build_master_router(model=Office, create_schema=OfficeCreate, read_schema=OfficeRead, entity_type="offices")
departments_router = build_master_router(
    model=Department, create_schema=DepartmentCreate, read_schema=DepartmentRead, entity_type="departments"
)
skills_router = build_master_router(model=Skill, create_schema=SkillCreate, read_schema=SkillRead, entity_type="skills")
client_groups_router = build_master_router(
    model=ClientGroup, create_schema=ClientGroupCreate, read_schema=ClientGroupRead, entity_type="client_groups"
)
