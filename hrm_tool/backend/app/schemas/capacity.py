import uuid

from pydantic import BaseModel


class StaffUtilisation(BaseModel):
    staff_id: uuid.UUID
    full_name: str
    designation: str
    net_capacity_hrs: float
    allocated_hrs: float
    soft_allocated_hrs: float
    chargeable_hrs: float
    available_hrs: float
    utilisation_pct: float
    chargeable_util_pct: float
    target_chargeability_pct: float | None
    bench_days: int


class RecomputeResponse(BaseModel):
    rows_written: int
    date_from: str
    date_to: str
