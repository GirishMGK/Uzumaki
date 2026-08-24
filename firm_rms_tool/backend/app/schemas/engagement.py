import uuid

from pydantic import BaseModel

from app.models.enums import (
    Complexity,
    EngagementStatus,
    FeeType,
    PeriodType,
    Priority,
    ServiceType,
)


class EngagementBase(BaseModel):
    engagement_code: str
    client_id: uuid.UUID
    department_id: uuid.UUID
    service_type: ServiceType
    financial_year: str
    period_type: PeriodType = PeriodType.ANNUAL
    period_from: str | None = None
    period_to: str | None = None
    signing_partner_id: uuid.UUID | None = None
    engagement_partner_id: uuid.UUID | None = None
    eqcr_partner_id: uuid.UUID | None = None
    eqcr_required: bool = False
    engagement_manager_id: uuid.UUID | None = None
    priority: Priority = Priority.P3_NORMAL
    complexity: Complexity = Complexity.MEDIUM
    fee_amount: float | None = None
    fee_type: FeeType | None = None
    fee_currency: str = "INR"
    out_of_pocket_budget: float | None = None
    billing_milestones: list[dict] | None = None
    budget_hours_total: float | None = None
    budget_hours_by_grade: dict[str, float] | None = None
    reporting_deadline: str | None = None
    statutory_due_date: str | None = None
    internal_target_date: str | None = None
    planned_start: str | None = None
    planned_end: str | None = None
    status: EngagementStatus = EngagementStatus.PIPELINE
    win_probability: int | None = None
    appointment_date: str | None = None
    first_year_of_appointment: int | None = None
    rotation_due_fy: str | None = None
    ep_rotation_due_fy: str | None = None
    requires_specialist_skills: list[uuid.UUID] | None = None
    udin: str | None = None
    report_signed_date: str | None = None
    prior_year_engagement_id: uuid.UUID | None = None
    notes: str | None = None


class EngagementCreate(EngagementBase):
    pass


class EngagementRead(EngagementBase):
    id: uuid.UUID
    is_active: bool

    @classmethod
    def from_orm_masked(cls, eng, *, mask_financials: bool) -> "EngagementRead":
        data = eng.model_dump()
        if mask_financials:
            data["fee_amount"] = None
            data["out_of_pocket_budget"] = None
            data["billing_milestones"] = None
        return cls(**data)


class EngagementRollForwardRequest(BaseModel):
    new_engagement_code: str
    new_financial_year: str | None = None
    date_shift_years: int = 1
    copy_team: bool = True


class EngagementRollForwardResponse(BaseModel):
    new_engagement: EngagementRead
    copied: list[dict]
    skipped: list[dict]
