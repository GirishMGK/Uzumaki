"""Shared mixins for every entity table.

Every table in this system gets: a UUID primary key, created/updated
audit columns, and soft delete. Nothing is ever hard-deleted — see
docs/business-rules.md ("the roster is audit evidence").
"""
import uuid
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampSoftDeleteMixin(SQLModel):
    """Adds created_at/updated_at/created_by/updated_by + is_active/deleted_at.

    Mix this into every entity. `created_by`/`updated_by` are staff UUIDs
    (as strings) but are NOT declared as FK here — the actor may be a system
    user_id from the `users` table; routers set these from the auth context.
    """

    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
    created_by: uuid.UUID | None = Field(default=None)
    updated_by: uuid.UUID | None = Field(default=None)
    is_active: bool = Field(default=True, nullable=False)
    deleted_at: datetime | None = Field(default=None)


class UUIDPKMixin(SQLModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
