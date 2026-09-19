"""Per-staff, per-day capacity and availability (§5).

Phase P3/P5 note: this module currently computes capacity synchronously,
on demand, from raw allocations/leave/holidays. §5 requires materialising
this into a `capacity_daily` table via a nightly job + synchronous
invalidation on mutation for reports spanning > 90 days — that
materialisation lands in Phase P5. The formulas here are the ones that
table will be built from, so report code can already depend on this module's
function signatures.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.models.allocation import Allocation, HolidayCalendar, NonAvailability
from app.models.enums import AllocationStatus, BookingType, DayFraction, NonAvailabilityStatus
from app.models.staff import Staff

WEEKEND_WEEKDAYS = {5, 6}  # Saturday, Sunday


def _to_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date() if isinstance(s, str) else s


def _daterange(d1: date, d2: date):
    for n in range((d2 - d1).days + 1):
        yield d1 + timedelta(n)


def _is_holiday(day: date, holidays: list[HolidayCalendar]) -> bool:
    return any(_to_date(h.holiday_date) == day for h in holidays)


def _leave_fraction_on(day: date, leaves: list[NonAvailability]) -> float:
    """Fraction of the day (0..1) consumed by approved leave."""
    fraction = 0.0
    for leave in leaves:
        if _to_date(leave.date_from) <= day <= _to_date(leave.date_to):
            fraction += 0.5 if leave.day_fraction in (DayFraction.FIRST_HALF.value, DayFraction.SECOND_HALF.value) else 1.0
    return min(fraction, 1.0)


def compute_net_capacity_hours(
    db: Session,
    staff_id: uuid.UUID,
    date_from: str | date,
    date_to: str | date,
) -> float:
    """gross_capacity_hrs - leave_deduction, summed over the range, per §5."""
    staff = db.get(Staff, staff_id)
    if staff is None:
        return 0.0
    d_from, d_to = _to_date(date_from), _to_date(date_to)
    gross_per_day = staff.standard_hours_per_week / 5

    holidays = list(
        db.exec(
            select(HolidayCalendar)
            .where(HolidayCalendar.is_active == True)  # noqa: E712
            .where(HolidayCalendar.holiday_date >= d_from.isoformat())
            .where(HolidayCalendar.holiday_date <= d_to.isoformat())
            .where((HolidayCalendar.office_id == staff.base_office_id) | (HolidayCalendar.office_id.is_(None)))  # type: ignore[union-attr]
        ).all()
    )
    leaves = list(
        db.exec(
            select(NonAvailability)
            .where(NonAvailability.staff_id == staff_id)
            .where(NonAvailability.is_active == True)  # noqa: E712
            .where(NonAvailability.status == NonAvailabilityStatus.APPROVED.value)
            .where(NonAvailability.date_from <= d_to.isoformat())
            .where(NonAvailability.date_to >= d_from.isoformat())
        ).all()
    )

    net_hours = 0.0
    for day in _daterange(d_from, d_to):
        if day.weekday() in WEEKEND_WEEKDAYS or _is_holiday(day, holidays):
            continue
        leave_fraction = _leave_fraction_on(day, leaves)
        net_hours += gross_per_day * (1 - leave_fraction)
    return round(net_hours, 2)


def compute_allocated_hours(
    db: Session,
    staff_id: uuid.UUID,
    date_from: str | date,
    date_to: str | date,
    *,
    include_soft: bool = False,
) -> float:
    """Sum of (allocation_pct x net day capacity) over CONFIRMED/IN_PROGRESS bookings."""
    staff = db.get(Staff, staff_id)
    if staff is None:
        return 0.0
    d_from, d_to = _to_date(date_from), _to_date(date_to)
    statuses = [AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS]
    if include_soft:
        statuses.append(AllocationStatus.PROPOSED)

    stmt = (
        select(Allocation)
        .where(Allocation.staff_id == staff_id)
        .where(Allocation.is_active == True)  # noqa: E712
        .where(Allocation.status.in_(statuses))  # type: ignore[attr-defined]
        .where(Allocation.date_from <= d_to.isoformat())
        .where(Allocation.date_to >= d_from.isoformat())
    )
    if not include_soft:
        stmt = stmt.where(Allocation.booking_type == BookingType.HARD)

    allocations = list(db.exec(stmt).all())
    gross_per_day = staff.standard_hours_per_week / 5

    total = 0.0
    for day in _daterange(d_from, d_to):
        if day.weekday() in WEEKEND_WEEKDAYS:
            continue
        day_pct = sum(
            a.allocation_pct
            for a in allocations
            if _to_date(a.date_from) <= day <= _to_date(a.date_to)
        )
        total += gross_per_day * (day_pct / 100)
    return round(total, 2)


def compute_utilisation(db: Session, staff_id: uuid.UUID, date_from: str | date, date_to: str | date) -> dict:
    net_capacity = compute_net_capacity_hours(db, staff_id, date_from, date_to)
    allocated = compute_allocated_hours(db, staff_id, date_from, date_to)
    util_pct = round((allocated / net_capacity) * 100, 2) if net_capacity else 0.0
    return {
        "staff_id": str(staff_id),
        "net_capacity_hrs": net_capacity,
        "allocated_hrs": allocated,
        "available_hrs": round(net_capacity - allocated, 2),
        "utilisation_pct": util_pct,
    }
