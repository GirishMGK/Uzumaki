"""What-if scenario planning (§8, Phase P10).

Not one of §3's ~17 tables — added the same way `capacity_daily` was in
P5: a table the spec's later sections (§8 "scenarios") require but §3
doesn't enumerate, so it's added here and documented as a deviation in
docs/decisions.md rather than silently expanding §3.

A `Scenario` is a named sandbox of hypothetical bookings
(`ScenarioAllocation`) that never touch the real `allocations` table until
someone explicitly promotes it. Each line still references real
`staff`/`engagements` rows (so the real conflict engine can be run against
it for an accurate impact check), but nothing here is booked until
`/scenarios/{id}/promote` writes real `Allocation` rows through the same
validate+write path `POST /allocations` uses.
"""
import uuid

from sqlmodel import Field

from app.models.base import TimestampSoftDeleteMixin, UUIDPKMixin
from app.models.enums import AllocationRole


class Scenario(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "scenarios"

    name: str = Field(nullable=False)
    description: str | None = Field(default=None)
    date_from: str = Field(nullable=False)
    date_to: str = Field(nullable=False)
    status: str = Field(default="DRAFT")  # DRAFT, PROMOTED, DISCARDED


class ScenarioAllocation(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "scenario_allocations"

    scenario_id: uuid.UUID = Field(foreign_key="scenarios.id", index=True, nullable=False)
    staff_id: uuid.UUID = Field(foreign_key="staff.id", index=True, nullable=False)
    engagement_id: uuid.UUID = Field(foreign_key="engagements.id", index=True, nullable=False)
    role_on_engagement: AllocationRole = Field(nullable=False)
    date_from: str = Field(nullable=False)
    date_to: str = Field(nullable=False)
    allocation_pct: float = Field(default=100)
    notes: str | None = Field(default=None)
    # Set once /promote successfully writes the real Allocation this line became.
    promoted_allocation_id: uuid.UUID | None = Field(default=None, foreign_key="allocations.id")
