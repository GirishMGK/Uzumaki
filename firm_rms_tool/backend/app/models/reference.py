"""Reference / master tables: offices, departments, grades, skills, app_config."""
import uuid

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field

from app.models.base import TimestampSoftDeleteMixin, UUIDPKMixin
from app.models.enums import Designation, SkillCategory


class Office(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "offices"

    code: str = Field(index=True, unique=True, nullable=False)
    name: str = Field(nullable=False)
    city: str = Field(nullable=False)
    state: str = Field(nullable=False)
    is_head_office: bool = Field(default=False)
    address: str | None = Field(default=None)
    timezone: str = Field(default="Asia/Kolkata")


class Department(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "departments"

    code: str = Field(index=True, unique=True, nullable=False)
    name: str = Field(nullable=False)
    head_staff_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")


class Grade(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    """designation <-> grade_rank <-> default chargeability % <-> default rates."""

    __tablename__ = "grades"

    designation: Designation = Field(nullable=False, unique=True)
    grade_rank: int = Field(nullable=False)
    default_chargeability_target_pct: float = Field(nullable=False)
    default_cost_rate_per_hour: float | None = Field(default=None)
    default_bill_rate_per_hour: float | None = Field(default=None)


class Skill(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "skills"

    code: str = Field(index=True, unique=True, nullable=False)
    name: str = Field(nullable=False)
    category: SkillCategory = Field(nullable=False)
    parent_skill_id: uuid.UUID | None = Field(default=None, foreign_key="skills.id")


class StaffSkill(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "staff_skills"

    staff_id: uuid.UUID = Field(foreign_key="staff.id", index=True, nullable=False)
    skill_id: uuid.UUID = Field(foreign_key="skills.id", index=True, nullable=False)
    proficiency: int = Field(nullable=False, ge=1, le=5)
    years_experience: float | None = Field(default=None)
    last_used_on: str | None = Field(default=None)
    evidence_engagement_id: uuid.UUID | None = Field(default=None, foreign_key="engagements.id")
    verified_by: uuid.UUID | None = Field(default=None)
    verified_on: str | None = Field(default=None)


class ActivityCode(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "activity_codes"

    code: str = Field(index=True, unique=True, nullable=False)
    name: str = Field(nullable=False)
    is_chargeable: bool = Field(default=True)


class AppConfig(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    """Editable rule thresholds (see §4) — key/value so no redeploy is needed."""

    __tablename__ = "app_config"

    key: str = Field(index=True, unique=True, nullable=False)
    value: dict = Field(sa_column=Column(JSON), default_factory=dict)
    description: str | None = Field(default=None)
