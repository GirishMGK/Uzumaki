import uuid

from pydantic import BaseModel

from app.schemas.staff import StaffRead


class MeProfileOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str
    full_name: str
    staff: StaffRead | None


class MeAllocationOut(BaseModel):
    id: uuid.UUID
    engagement_code: str
    client_name: str
    role_on_engagement: str
    date_from: str
    date_to: str
    allocation_pct: float
    status: str
    work_location: str


class MeLeaveBalanceOut(BaseModel):
    financial_year_from: str
    financial_year_to: str
    entitlement_days: float | None
    approved_days_taken: float
    pending_days: float
    remaining_days: float | None
