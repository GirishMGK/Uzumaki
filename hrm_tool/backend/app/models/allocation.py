"""The core scheduling table (§3.8) plus its directly related tables."""
import uuid

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field

from app.models.base import TimestampSoftDeleteMixin, UUIDPKMixin
from app.models.enums import AllocationRole, AllocationStatus, BookingType, WorkLocation


class Allocation(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "allocations"

    engagement_id: uuid.UUID = Field(foreign_key="engagements.id", index=True, nullable=False)
    staff_id: uuid.UUID = Field(foreign_key="staff.id", index=True, nullable=False)
    role_on_engagement: AllocationRole = Field(nullable=False)
    department_id: uuid.UUID | None = Field(default=None, foreign_key="departments.id")
    reporting_manager_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")
    partner_id: uuid.UUID | None = Field(default=None, foreign_key="staff.id")
    office_id: uuid.UUID | None = Field(default=None, foreign_key="offices.id")
    work_location: WorkLocation = Field(default=WorkLocation.OFFICE)

    date_from: str = Field(nullable=False, index=True)
    date_to: str = Field(nullable=False, index=True)
    allocation_pct: float = Field(default=100)
    planned_hours: float | None = Field(default=None)

    status: AllocationStatus = Field(default=AllocationStatus.DRAFT, index=True)
    booking_type: BookingType = Field(default=BookingType.HARD)
    is_chargeable: bool = Field(default=True)

    requested_by: uuid.UUID | None = Field(default=None)
    approved_by: uuid.UUID | None = Field(default=None)
    approved_on: str | None = Field(default=None)
    cancellation_reason: str | None = Field(default=None)
    override_flags: list | None = Field(default=None, sa_column=Column(JSON))

    notes: str | None = Field(default=None)
    version: int = Field(default=1)


class NonAvailability(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "non_availability"

    staff_id: uuid.UUID = Field(foreign_key="staff.id", index=True, nullable=False)
    type: str = Field(nullable=False)  # NonAvailabilityType
    date_from: str = Field(nullable=False, index=True)
    date_to: str = Field(nullable=False, index=True)
    day_fraction: str = Field(default="FULL")  # DayFraction
    status: str = Field(default="APPLIED", index=True)  # NonAvailabilityStatus
    approved_by: uuid.UUID | None = Field(default=None)
    reason: str | None = Field(default=None)
    counts_against_entitlement: bool = Field(default=True)


class HolidayCalendar(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "holiday_calendar"

    office_id: uuid.UUID | None = Field(default=None, foreign_key="offices.id")  # null = firm-wide
    holiday_date: str = Field(nullable=False, index=True)
    name: str = Field(nullable=False)
    is_optional: bool = Field(default=False)


class Timesheet(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "timesheets"

    staff_id: uuid.UUID = Field(foreign_key="staff.id", index=True, nullable=False)
    engagement_id: uuid.UUID = Field(foreign_key="engagements.id", index=True, nullable=False)
    allocation_id: uuid.UUID | None = Field(default=None, foreign_key="allocations.id")
    work_date: str = Field(nullable=False, index=True)
    hours: float = Field(nullable=False)
    is_chargeable: bool = Field(default=True)
    activity_code: str | None = Field(default=None)
    narration: str | None = Field(default=None)
    status: str = Field(default="DRAFT")  # TimesheetStatus
    approved_by: uuid.UUID | None = Field(default=None)
    approved_on: str | None = Field(default=None)


class IndependenceDeclaration(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "independence_declarations"

    staff_id: uuid.UUID = Field(foreign_key="staff.id", index=True, nullable=False)
    client_id: uuid.UUID = Field(foreign_key="clients.id", index=True, nullable=False)
    declaration_fy: str = Field(nullable=False)
    has_financial_interest: bool = Field(default=False)
    has_relative_in_client: bool = Field(default=False)
    held_employment_last_2yrs: bool = Field(default=False)
    has_loan_from_client: bool = Field(default=False)
    other_threat_description: str | None = Field(default=None)
    is_conflicted: bool = Field(default=False, index=True)
    declared_on: str | None = Field(default=None)
    reviewed_by: uuid.UUID | None = Field(default=None)


class ResourceRequest(UUIDPKMixin, TimestampSoftDeleteMixin, table=True):
    __tablename__ = "resource_requests"

    engagement_id: uuid.UUID = Field(foreign_key="engagements.id", index=True, nullable=False)
    requested_by: uuid.UUID | None = Field(default=None)
    required_grade: str | None = Field(default=None)
    required_skills: list | None = Field(default=None, sa_column=Column(JSON))
    required_office: uuid.UUID | None = Field(default=None, foreign_key="offices.id")
    date_from: str = Field(nullable=False)
    date_to: str = Field(nullable=False)
    headcount: int = Field(default=1)
    status: str = Field(default="OPEN")  # ResourceRequestStatus
    fulfilment_allocation_ids: list | None = Field(default=None, sa_column=Column(JSON))
    priority: str | None = Field(default=None)
    requested_on: str | None = Field(default=None)
    sla_due_on: str | None = Field(default=None)
