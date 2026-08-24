import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.core.deps import get_client_ip, get_current_user, get_db, require_roles
from app.core.soft_delete import reject_hard_delete
from app.models.client import Client
from app.models.enums import AuditAction, UserRole
from app.models.user import User
from app.schemas.client import ClientCreate, ClientRead

router = APIRouter()

WRITE_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER)


@router.get("", response_model=list[ClientRead])
def list_clients(
    skip: int = 0,
    limit: int = 100,
    group_id: uuid.UUID | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ClientRead]:
    stmt = select(Client).where(Client.is_active == True)  # noqa: E712
    if group_id:
        stmt = stmt.where(Client.group_id == group_id)
    if q:
        stmt = stmt.where(Client.name.contains(q))  # type: ignore[union-attr]
    stmt = stmt.offset(skip).limit(limit)
    return list(db.exec(stmt).all())


@router.get("/{client_id}", response_model=ClientRead)
def get_client(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ClientRead:
    row = db.get(Client, client_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Client not found")
    return row


@router.post("", response_model=ClientRead, status_code=status.HTTP_201_CREATED)
def create_client(
    payload: ClientCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> ClientRead:
    existing = db.exec(select(Client).where(Client.client_code == payload.client_code)).first()
    if existing:
        raise HTTPException(status_code=409, detail="client_code already exists")
    row = Client(**payload.model_dump(), created_by=user.id, updated_by=user.id)
    db.add(row)
    db.flush()
    write_audit_log(
        db, entity_type="clients", entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
        before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{client_id}")
def delete_client(
    client_id: uuid.UUID,
    request: Request,
    hard: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN)),
) -> dict:
    reject_hard_delete(hard)
    row = db.get(Client, client_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Client not found")
    before = row.model_copy()
    row.is_active = False
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="clients", entity_id=row.id, action=AuditAction.DELETE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"status": "soft_deleted", "id": str(client_id)}
