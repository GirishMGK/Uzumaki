"""Materialises `capacity_daily` per §5's formulas.

Batch-oriented on purpose: fetch every input (holidays, leaves,
allocations) for the whole staff set and date range in a handful of
queries, then loop staff x day in Python with each staff's own small
allocation/leave list — not a per-staff-per-day query. That's what keeps
this fast enough for the P5 DoD ("utilisation for 300 staff x 90 days
computes in < 2s from the materialised table" — this module is what
populates that table; app/services/capacity.py's report-side read is the
one that has to hit the 2s number, but this is what makes that possible by
doing the O(staff x days) work once, off the request path).

Two invocations:
- `app/jobs/capacity_job.py` calls `recompute_range` nightly for a wide
  rolling window.
- Allocation/leave mutation routers call `recompute_range` synchronously
  for just the affected staff + a window covering the mutation's dates,
  per §5's "invalidate/recompute synchronously on every allocation or
  leave mutation."
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.app_config import get_config_value
from app.core.config import get_settings
from app.models.allocation import Allocation, HolidayCalendar, NonAvailability
from app.models.capacity import CapacityDaily
from app.models.enums import AllocationStatus, BookingType, DayFraction, NonAvailabilityStatus
from app.models.staff import Staff

WEEKEND_WEEKDAYS = {5, 6}


def _to_date(s: str | date) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date() if isinstance(s, str) else s


def _daterange(d1: date, d2: date):
    for n in range((d2 - d1).days + 1):
        yield d1 + timedelta(n)


def recompute_range(
    db: Session,
    date_from: date,
    date_to: date,
    staff_ids: list[uuid.UUID] | None = None,
) -> int:
    """Recompute + upsert capacity_daily rows for staff_ids (or every active
    staff member) over [date_from, date_to]. Returns rows written.
    """
    staff_stmt = select(Staff).where(Staff.is_active == True)  # noqa: E712
    if staff_ids:
        staff_stmt = staff_stmt.where(Staff.id.in_(staff_ids))  # type: ignore[attr-defined]
    staff_list = list(db.exec(staff_stmt).all())
    if not staff_list:
        return 0
    ids = [s.id for s in staff_list]

    bench_days = int(get_config_value(db, "bench_days", get_settings().default_bench_days))

    holidays = list(
        db.exec(
            select(HolidayCalendar)
            .where(HolidayCalendar.is_active == True)  # noqa: E712
            .where(HolidayCalendar.holiday_date >= date_from.isoformat())
            .where(HolidayCalendar.holiday_date <= date_to.isoformat())
        ).all()
    )
    holidays_firmwide: set[date] = set()
    holidays_by_office: dict[uuid.UUID, set[date]] = defaultdict(set)
    for h in holidays:
        d = _to_date(h.holiday_date)
        if h.office_id is None:
            holidays_firmwide.add(d)
        else:
            holidays_by_office[h.office_id].add(d)

    leaves = list(
        db.exec(
            select(NonAvailability)
            .where(NonAvailability.staff_id.in_(ids))  # type: ignore[attr-defined]
            .where(NonAvailability.is_active == True)  # noqa: E712
            .where(NonAvailability.status == NonAvailabilityStatus.APPROVED.value)
            .where(NonAvailability.date_from <= date_to.isoformat())
            .where(NonAvailability.date_to >= date_from.isoformat())
        ).all()
    )
    leaves_by_staff: dict[uuid.UUID, list[NonAvailability]] = defaultdict(list)
    for lv in leaves:
        leaves_by_staff[lv.staff_id].append(lv)

    allocations = list(
        db.exec(
            select(Allocation)
            .where(Allocation.staff_id.in_(ids))  # type: ignore[attr-defined]
            .where(Allocation.is_active == True)  # noqa: E712
            .where(Allocation.date_from <= date_to.isoformat())
            .where(Allocation.date_to >= date_from.isoformat())
            .where(
                Allocation.status.in_(  # type: ignore[attr-defined]
                    [AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS, AllocationStatus.PROPOSED]
                )
            )
        ).all()
    )
    hard_by_staff: dict[uuid.UUID, list[Allocation]] = defaultdict(list)
    soft_by_staff: dict[uuid.UUID, list[Allocation]] = defaultdict(list)
    for a in allocations:
        is_hard = a.status in (AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS) and a.booking_type == BookingType.HARD
        if is_hard:
            hard_by_staff[a.staff_id].append(a)
        else:
            soft_by_staff[a.staff_id].append(a)

    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[CapacityDaily] = []

    for staff in staff_list:
        gross_per_day = staff.standard_hours_per_week / 5
        office_holidays = holidays_by_office.get(staff.base_office_id, set()) if staff.base_office_id else set()
        my_leaves = leaves_by_staff.get(staff.id, [])
        my_hard = hard_by_staff.get(staff.id, [])
        my_soft = soft_by_staff.get(staff.id, [])
        consecutive_free = 0

        for day in _daterange(date_from, date_to):
            is_weekend = day.weekday() in WEEKEND_WEEKDAYS
            is_holiday = day in holidays_firmwide or day in office_holidays
            gross = 0.0 if (is_weekend or is_holiday) else gross_per_day

            leave_fraction = 0.0
            for lv in my_leaves:
                if _to_date(lv.date_from) <= day <= _to_date(lv.date_to):
                    leave_fraction += 0.5 if lv.day_fraction in (DayFraction.FIRST_HALF.value, DayFraction.SECOND_HALF.value) else 1.0
            leave_fraction = min(leave_fraction, 1.0)
            leave_ded = gross * leave_fraction
            net = gross - leave_ded

            hard_pct = sum(a.allocation_pct for a in my_hard if _to_date(a.date_from) <= day <= _to_date(a.date_to))
            chargeable_pct = sum(
                a.allocation_pct for a in my_hard if a.is_chargeable and _to_date(a.date_from) <= day <= _to_date(a.date_to)
            )
            soft_pct = sum(a.allocation_pct for a in my_soft if _to_date(a.date_from) <= day <= _to_date(a.date_to))

            allocated_hrs = net * (hard_pct / 100)
            chargeable_hrs = net * (chargeable_pct / 100)
            soft_allocated_hrs = net * (soft_pct / 100)
            available_hrs = net - allocated_hrs
            util_pct = (allocated_hrs / net * 100) if net > 0 else 0.0
            chg_util_pct = (chargeable_hrs / net * 100) if net > 0 else 0.0

            if not is_weekend and not is_holiday:
                if hard_pct == 0:
                    consecutive_free += 1
                else:
                    consecutive_free = 0
            bench_flag = consecutive_free >= bench_days

            rows.append(
                CapacityDaily(
                    staff_id=staff.id, capacity_date=day,
                    gross_capacity_hrs=round(gross, 2), leave_deduction_hrs=round(leave_ded, 2),
                    net_capacity_hrs=round(net, 2), allocated_hrs=round(allocated_hrs, 2),
                    soft_allocated_hrs=round(soft_allocated_hrs, 2), chargeable_hrs=round(chargeable_hrs, 2),
                    available_hrs=round(available_hrs, 2), utilisation_pct=round(util_pct, 2),
                    chargeable_util_pct=round(chg_util_pct, 2), bench_flag=bench_flag, computed_at=now_iso,
                )
            )

    db.execute(
        delete(CapacityDaily)
        .where(CapacityDaily.staff_id.in_(ids))  # type: ignore[attr-defined]
        .where(CapacityDaily.capacity_date >= date_from)
        .where(CapacityDaily.capacity_date <= date_to)
    )
    db.add_all(rows)
    db.commit()
    return len(rows)
