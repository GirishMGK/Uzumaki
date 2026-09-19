"""`capacity_daily` — the materialised per-staff, per-day capacity table (§5).

Not part of §3's table list verbatim, but §5 explicitly requires it:
"Materialise into `capacity_daily` ... via a nightly APScheduler job **and**
invalidate/recompute synchronously on every allocation or leave mutation.
Reports must never recompute from raw allocations at query time for ranges
> 90 days."

One row per (staff_id, date). Upserted, never soft-deleted individually —
the whole table is a derived cache and can be dropped/rebuilt safely
(`app/services/capacity_materializer.py` is the only writer).
"""
import uuid
from datetime import date

from sqlmodel import Field, SQLModel


class CapacityDaily(SQLModel, table=True):
    __tablename__ = "capacity_daily"
    __table_args__ = {"sqlite_autoincrement": False}

    staff_id: uuid.UUID = Field(foreign_key="staff.id", primary_key=True)
    # Named capacity_date, not `date`, to avoid a pydantic field-name/type-name
    # collision with the `date` class imported above (SQLModel/pydantic
    # forward-ref resolution fails to build the model otherwise).
    capacity_date: date = Field(primary_key=True, index=True)

    gross_capacity_hrs: float = Field(default=0)
    leave_deduction_hrs: float = Field(default=0)
    net_capacity_hrs: float = Field(default=0)
    allocated_hrs: float = Field(default=0)          # CONFIRMED/IN_PROGRESS, HARD only
    soft_allocated_hrs: float = Field(default=0)      # + PROPOSED / SOFT booking_type
    chargeable_hrs: float = Field(default=0)
    available_hrs: float = Field(default=0)
    utilisation_pct: float = Field(default=0)
    chargeable_util_pct: float = Field(default=0)
    bench_flag: bool = Field(default=False)
    computed_at: str | None = Field(default=None)
