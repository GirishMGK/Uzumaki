import uuid

from pydantic import BaseModel

from app.models.enums import DayFraction, NonAvailabilityStatus, NonAvailabilityType


class NonAvailabilityCreate(BaseModel):
    staff_id: uuid.UUID
    type: NonAvailabilityType
    date_from: str
    date_to: str
    day_fraction: DayFraction = DayFraction.FULL
    reason: str | None = None
    counts_against_entitlement: bool = True


class NonAvailabilityRead(BaseModel):
    id: uuid.UUID
    staff_id: uuid.UUID
    type: str
    date_from: str
    date_to: str
    day_fraction: str
    status: str
    approved_by: uuid.UUID | None = None
    reason: str | None = None
    counts_against_entitlement: bool
    is_active: bool
