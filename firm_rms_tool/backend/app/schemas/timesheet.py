import uuid

from pydantic import BaseModel


class TimesheetCreate(BaseModel):
    staff_id: uuid.UUID
    engagement_id: uuid.UUID
    allocation_id: uuid.UUID | None = None
    work_date: str
    hours: float
    is_chargeable: bool = True
    activity_code: str | None = None
    narration: str | None = None


class TimesheetUpdate(BaseModel):
    work_date: str | None = None
    hours: float | None = None
    is_chargeable: bool | None = None
    activity_code: str | None = None
    narration: str | None = None


class TimesheetRead(BaseModel):
    id: uuid.UUID
    staff_id: uuid.UUID
    engagement_id: uuid.UUID
    allocation_id: uuid.UUID | None
    work_date: str
    hours: float
    is_chargeable: bool
    activity_code: str | None
    narration: str | None
    status: str
    approved_by: uuid.UUID | None
    approved_on: str | None
    is_active: bool
