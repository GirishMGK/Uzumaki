"""The engagement — the unit of work (§3.5)."""
import uuid

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field

from app.models.base import TimestampSoftDeleteMixin, UUIDPKMixin
from app.models.enums import (
    Complexity,
    EngagementStatus,
    FeeType,
    PeriodType,
    Priority,
    ServiceType,
)


class Engagement(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "engagements"

    engagement_code: str = Field(index=True, unique=True, nullable=False)
    client_id: uuid.UUID = Field(foreign_key="clients.id", index=True, nullable=False)
    department_id: uuid.UUID = Field(foreign_key="departments.id", index=True, nullable=False)
    service_type: ServiceType = Field(nullable=False)

    financial_year: str = Field(nullable=False, index=True)
    period_type: PeriodType = Field(default=PeriodType.ANNUAL)
    period_from: str | None = Field(default=None)
    period_to: str | None = Field(default=None)

    signing_partner_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")
    engagement_partner_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id", index=True)
    eqcr_partner_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")
    eqcr_required: bool = Field(default=False)
    engagement_manager_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")

    priority: Priority = Field(default=Priority.P3_NORMAL)
    complexity: Complexity = Field(default=Complexity.MEDIUM)

    fee_amount: float | None = Field(default=None)
    fee_type: FeeType | None = Field(default=None)
    fee_currency: str = Field(default="INR")
    out_of_pocket_budget: float | None = Field(default=None)
    billing_milestones: list | None = Field(default=None, sa_column=Column(JSON))
    budget_hours_total: float | None = Field(default=None)
    budget_hours_by_grade: dict | None = Field(default=None, sa_column=Column(JSON))

    reporting_deadline: str | None = Field(default=None, index=True)
    statutory_due_date: str | None = Field(default=None)
    internal_target_date: str | None = Field(default=None)
    planned_start: str | None = Field(default=None)
    planned_end: str | None = Field(default=None)

    status: EngagementStatus = Field(default=EngagementStatus.PIPELINE, index=True)
    win_probability: int | None = Field(default=None)

    appointment_date: str | None = Field(default=None)
    first_year_of_appointment: int | None = Field(default=None)
    rotation_due_fy: str | None = Field(default=None)
    ep_rotation_due_fy: str | None = Field(default=None)

    requires_specialist_skills: list | None = Field(default=None, sa_column=Column(JSON))
    udin: str | None = Field(default=None)
    report_signed_date: str | None = Field(default=None)
    prior_year_engagement_id: uuid.UUID | None = Field(default=None, foreign_key="engagements.id")
    notes: str | None = Field(default=None)


# Fields masked (returned as null) for RBAC roles without financial visibility.
ENGAGEMENT_FINANCIAL_FIELDS = {
    "fee_amount",
    "out_of_pocket_budget",
    "billing_milestones",
}
