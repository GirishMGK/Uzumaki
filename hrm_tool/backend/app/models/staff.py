"""The staff master — partners, employees and articles in one table (§3.6)."""
import uuid

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field

from app.models.base import TimestampSoftDeleteMixin, UUIDPKMixin
from app.models.enums import Designation, EmploymentStatus, EmploymentType, StaffCategory


class Staff(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "staff"

    employee_code: str = Field(index=True, unique=True, nullable=False)
    full_name: str = Field(nullable=False)
    short_name: str | None = Field(default=None)
    personal_email: str | None = Field(default=None)
    official_email: str | None = Field(default=None, index=True)
    mobile: str | None = Field(default=None)

    staff_category: StaffCategory = Field(nullable=False)
    designation: Designation = Field(nullable=False)
    grade_rank: int = Field(nullable=False)
    qualification: list | None = Field(default=None, sa_column=Column(JSON))
    icai_membership_no: str | None = Field(default=None)
    icai_frn_association: str | None = Field(default=None)
    icai_srn: str | None = Field(default=None)
    certifications: list | None = Field(default=None, sa_column=Column(JSON))

    base_office_id: uuid.UUID | None = Field(default=None, foreign_key="offices.id", index=True)
    current_office_id: uuid.UUID | None = Field(default=None, foreign_key="offices.id", index=True)
    home_city: str | None = Field(default=None)
    willing_to_travel: bool = Field(default=True)
    max_outstation_days_per_month: int | None = Field(default=None)

    primary_department_id: uuid.UUID | None = Field(default=None, foreign_key="departments.id", index=True)
    secondary_department_ids: list | None = Field(default=None, sa_column=Column(JSON))
    reporting_manager_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")
    partner_mentor_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")

    date_of_joining: str | None = Field(default=None)
    date_of_exit: str | None = Field(default=None)
    notice_period_end: str | None = Field(default=None)
    employment_status: EmploymentStatus = Field(default=EmploymentStatus.ACTIVE)
    employment_type: EmploymentType = Field(default=EmploymentType.FULL_TIME)
    standard_hours_per_week: float = Field(default=40)
    chargeability_target_pct: float | None = Field(default=None)
    cost_rate_per_hour: float | None = Field(default=None)
    bill_rate_per_hour: float | None = Field(default=None)

    # Article-specific (null for everyone else)
    articleship_start_date: str | None = Field(default=None)
    articleship_end_date: str | None = Field(default=None)
    articleship_principal_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")
    icai_form_103_date: str | None = Field(default=None)
    icai_form_108_date: str | None = Field(default=None)
    secondment_flag: bool = Field(default=False)
    industrial_training_flag: bool = Field(default=False)
    leave_entitlement_days: float | None = Field(default=None)
    exam_leave_blocks: list | None = Field(default=None, sa_column=Column(JSON))

    photo_url: str | None = Field(default=None)
