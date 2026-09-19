import uuid

from pydantic import BaseModel

from app.models.enums import AllocationRole, AllocationStatus, BookingType, WorkLocation


class AllocationValidateRequest(BaseModel):
    engagement_id: uuid.UUID
    staff_id: uuid.UUID
    role_on_engagement: AllocationRole
    date_from: str
    date_to: str
    allocation_pct: float = 100
    status: AllocationStatus = AllocationStatus.CONFIRMED
    exclude_allocation_id: uuid.UUID | None = None
    office_id: uuid.UUID | None = None  # feeds R16/R17 (outstation / location-mismatch)
    work_location: WorkLocation | None = None


class RuleViolationOut(BaseModel):
    code: str
    severity: str
    message: str
    context: dict = {}
    overridable: bool = False
    override_role: str | None = None


class AllocationValidateResponse(BaseModel):
    is_blocked: bool
    violations: list[RuleViolationOut]


class OverrideEntry(BaseModel):
    code: str
    reason: str


class AllocationCreate(BaseModel):
    engagement_id: uuid.UUID
    staff_id: uuid.UUID
    role_on_engagement: AllocationRole
    department_id: uuid.UUID | None = None
    reporting_manager_id: uuid.UUID | None = None
    partner_id: uuid.UUID | None = None
    office_id: uuid.UUID | None = None
    work_location: WorkLocation = WorkLocation.OFFICE
    date_from: str
    date_to: str
    allocation_pct: float = 100
    planned_hours: float | None = None
    status: AllocationStatus = AllocationStatus.DRAFT
    booking_type: BookingType = BookingType.HARD
    is_chargeable: bool = True
    notes: str | None = None
    overrides: list[OverrideEntry] = []


class AllocationUpdate(BaseModel):
    role_on_engagement: AllocationRole | None = None
    department_id: uuid.UUID | None = None
    reporting_manager_id: uuid.UUID | None = None
    partner_id: uuid.UUID | None = None
    office_id: uuid.UUID | None = None
    work_location: WorkLocation | None = None
    date_from: str | None = None
    date_to: str | None = None
    allocation_pct: float | None = None
    planned_hours: float | None = None
    status: AllocationStatus | None = None
    booking_type: BookingType | None = None
    is_chargeable: bool | None = None
    notes: str | None = None
    cancellation_reason: str | None = None
    overrides: list[OverrideEntry] = []


class AllocationRead(BaseModel):
    id: uuid.UUID
    engagement_id: uuid.UUID
    staff_id: uuid.UUID
    role_on_engagement: str
    department_id: uuid.UUID | None
    reporting_manager_id: uuid.UUID | None
    partner_id: uuid.UUID | None
    office_id: uuid.UUID | None
    work_location: str
    date_from: str
    date_to: str
    allocation_pct: float
    planned_hours: float | None
    status: str
    booking_type: str
    is_chargeable: bool
    override_flags: list | None
    notes: str | None
    version: int
    is_active: bool
