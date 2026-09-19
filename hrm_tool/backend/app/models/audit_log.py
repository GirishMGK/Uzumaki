"""Append-only audit trail — SQC1/ISQM1 evidence of every mutation.

No delete endpoint exists for this table anywhere in the app (see
app/api/v1/audit.py). Retention is a data-lifecycle/backup policy question
(>= 8 years), not something enforced by application code.
"""
import uuid
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel
from sqlalchemy import Column
from sqlalchemy.types import JSON

from app.models.enums import AuditAction


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    entity_type: str = Field(index=True, nullable=False)
    entity_id: str = Field(index=True, nullable=False)
    action: AuditAction = Field(nullable=False)
    actor_id: uuid.UUID | None = Field(default=None, index=True)
    before: dict | None = Field(default=None, sa_column=Column(JSON))
    after: dict | None = Field(default=None, sa_column=Column(JSON))
    ip: str | None = Field(default=None)
    user_agent: str | None = Field(default=None)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
