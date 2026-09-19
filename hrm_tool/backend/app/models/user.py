"""Login/auth identity.

Not spec'd as a table in §3 — the spec's `staff` table models the HR/roster
record, not login credentials. We add a thin `users` table linked 1:1 (via
`staff_id`, nullable so pure system/admin accounts can exist without a
roster record) to carry the login role. See docs/decisions.md.
"""
import uuid

from sqlmodel import Field

from app.models.base import TimestampSoftDeleteMixin, UUIDPKMixin
from app.models.enums import UserRole


class User(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "users"

    email: str = Field(index=True, unique=True, nullable=False)
    hashed_password: str = Field(nullable=False)
    role: UserRole = Field(nullable=False)
    staff_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")
    full_name: str = Field(nullable=False)
    must_change_password: bool = Field(default=False)
    last_login_at: str | None = Field(default=None)
