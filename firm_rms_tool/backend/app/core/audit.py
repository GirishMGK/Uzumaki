"""Helper to write an audit_log row alongside every mutation.

Called explicitly from service/router code (not via ORM events) so that
`before`/`after` are always the exact JSON-serialisable payloads we intend
to record, and so a single caller-controlled transaction covers both the
entity write and the audit row (T12: exactly one audit_log row per mutation).
"""
import json
import uuid
from datetime import date, datetime
from typing import Any

from sqlmodel import Session, SQLModel

from app.models.audit_log import AuditLog
from app.models.enums import AuditAction


def _jsonable(obj: Any) -> Any:
    """Round-trip through pydantic/JSON encoding so UUID/date/enum survive."""
    if obj is None:
        return None
    if isinstance(obj, SQLModel):
        data = obj.model_dump(mode="json")
        return data
    if isinstance(obj, dict):
        return json.loads(json.dumps(obj, default=str))
    return obj


def write_audit_log(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID | str,
    action: AuditAction,
    actor_id: uuid.UUID | None,
    before: Any = None,
    after: Any = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        entity_type=entity_type,
        entity_id=str(entity_id),
        action=action,
        actor_id=actor_id,
        before=_jsonable(before),
        after=_jsonable(after),
        ip=ip,
        user_agent=user_agent,
    )
    session.add(entry)
    return entry
