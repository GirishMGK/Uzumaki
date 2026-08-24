import uuid

from pydantic import BaseModel

from app.models.enums import Designation, EmploymentStatus, EmploymentType, StaffCategory


class StaffBase(BaseModel):
    employee_code: str
    full_name: str
    short_name: str | None = None
    personal_email: str | None = None
    official_email: str | None = None
    mobile: str | None = None
    staff_category: StaffCategory
    designation: Designation
    grade_rank: int
    qualification: list[str] | None = None
    icai_membership_no: str | None = None
    icai_frn_association: str | None = None
    icai_srn: str | None = None
    certifications: list[str] | None = None
    base_office_id: uuid.UUID | None = None
    current_office_id: uuid.UUID | None = None
    home_city: str | None = None
    willing_to_travel: bool = True
    max_outstation_days_per_month: int | None = None
    primary_department_id: uuid.UUID | None = None
    secondary_department_ids: list[uuid.UUID] | None = None
    reporting_manager_id: uuid.UUID | None = None
    partner_mentor_id: uuid.UUID | None = None
    date_of_joining: str | None = None
    date_of_exit: str | None = None
    notice_period_end: str | None = None
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE
    employment_type: EmploymentType = EmploymentType.FULL_TIME
    standard_hours_per_week: float = 40
    chargeability_target_pct: float | None = None
    cost_rate_per_hour: float | None = None
    bill_rate_per_hour: float | None = None
    articleship_start_date: str | None = None
    articleship_end_date: str | None = None
    articleship_principal_id: uuid.UUID | None = None
    icai_form_103_date: str | None = None
    icai_form_108_date: str | None = None
    secondment_flag: bool = False
    industrial_training_flag: bool = False
    leave_entitlement_days: float | None = None
    exam_leave_blocks: list[dict] | None = None
    photo_url: str | None = None


class StaffCreate(StaffBase):
    pass


class StaffUpdate(BaseModel):
    """All fields optional for PATCH-style partial update."""

    employee_code: str | None = None
    full_name: str | None = None
    short_name: str | None = None
    personal_email: str | None = None
    official_email: str | None = None
    mobile: str | None = None
    staff_category: StaffCategory | None = None
    designation: Designation | None = None
    grade_rank: int | None = None
    qualification: list[str] | None = None
    icai_membership_no: str | None = None
    icai_frn_association: str | None = None
    icai_srn: str | None = None
    certifications: list[str] | None = None
    base_office_id: uuid.UUID | None = None
    current_office_id: uuid.UUID | None = None
    home_city: str | None = None
    willing_to_travel: bool | None = None
    max_outstation_days_per_month: int | None = None
    primary_department_id: uuid.UUID | None = None
    secondary_department_ids: list[uuid.UUID] | None = None
    reporting_manager_id: uuid.UUID | None = None
    partner_mentor_id: uuid.UUID | None = None
    date_of_joining: str | None = None
    date_of_exit: str | None = None
    notice_period_end: str | None = None
    employment_status: EmploymentStatus | None = None
    employment_type: EmploymentType | None = None
    standard_hours_per_week: float | None = None
    chargeability_target_pct: float | None = None
    cost_rate_per_hour: float | None = None
    bill_rate_per_hour: float | None = None
    articleship_start_date: str | None = None
    articleship_end_date: str | None = None
    articleship_principal_id: uuid.UUID | None = None
    icai_form_103_date: str | None = None
    icai_form_108_date: str | None = None
    secondment_flag: bool | None = None
    industrial_training_flag: bool | None = None
    leave_entitlement_days: float | None = None
    exam_leave_blocks: list[dict] | None = None
    photo_url: str | None = None


class StaffRead(StaffBase):
    id: uuid.UUID
    is_active: bool

    @classmethod
    def from_orm_masked(cls, staff, *, mask_financials: bool) -> "StaffRead":
        data = staff.model_dump()
        if mask_financials:
            data["cost_rate_per_hour"] = None
            data["bill_rate_per_hour"] = None
        return cls(**data)
