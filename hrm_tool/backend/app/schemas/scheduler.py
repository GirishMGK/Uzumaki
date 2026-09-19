import uuid

from pydantic import BaseModel


class BoardStaff(BaseModel):
    id: uuid.UUID
    employee_code: str
    full_name: str
    staff_category: str
    designation: str
    grade_rank: int
    base_office_id: uuid.UUID | None
    primary_department_id: uuid.UUID | None
    standard_hours_per_week: float


class BoardAllocation(BaseModel):
    id: uuid.UUID
    staff_id: uuid.UUID
    engagement_id: uuid.UUID
    engagement_code: str
    client_name: str
    department_id: uuid.UUID | None
    role_on_engagement: str
    date_from: str
    date_to: str
    allocation_pct: float
    status: str
    booking_type: str
    version: int


class BoardLeave(BaseModel):
    id: uuid.UUID
    staff_id: uuid.UUID
    type: str
    date_from: str
    date_to: str
    day_fraction: str
    status: str


class BoardHoliday(BaseModel):
    holiday_date: str
    name: str
    office_id: uuid.UUID | None


class BoardResponse(BaseModel):
    staff: list[BoardStaff]
    allocations: list[BoardAllocation]
    leaves: list[BoardLeave]
    holidays: list[BoardHoliday]


class EngagementLookupItem(BaseModel):
    id: uuid.UUID
    engagement_code: str
    client_name: str
    department_id: uuid.UUID
    status: str
    eqcr_partner_id: uuid.UUID | None
