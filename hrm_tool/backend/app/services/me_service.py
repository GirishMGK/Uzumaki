"""Self-service reads for the logged-in user (§10.2 "mobile /me", Phase P11).

Every function here takes the caller's own `staff_id` — there is no
"whose record" parameter anywhere in this module, deliberately: `/me` can
only ever be about the caller. Broader queries (anyone's allocations,
anyone's leave) already exist on the regular resource endpoints, gated by
the normal RBAC rules.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum

from sqlmodel import Session, select

from app.models.allocation import Allocation, NonAvailability, Timesheet
from app.models.client import Client
from app.models.engagement import Engagement
from app.models.enums import AllocationStatus, TimesheetStatus
from app.models.staff import Staff


def _plain(value: str | Enum) -> str:
    """`(str, Enum)` mixins (every enum column in this app) format as
    "ClassName.MEMBER" in an f-string/`str()` call on Python < 3.12 — a
    well-known gotcha. Everywhere here builds plain text (ICS, digest
    emails) rather than going through a pydantic response model (which
    serializes these correctly on its own), so it needs an explicit
    `.value` unwrap. Accepts a plain str too, since SQLAlchemy sometimes
    hands back the raw column value rather than the enum member."""
    return value.value if isinstance(value, Enum) else value


def _to_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _overlap_days(a_from: str, a_to: str, w_from: date, w_to: date) -> int:
    start = max(_to_date(a_from), w_from)
    end = min(_to_date(a_to), w_to)
    return max(0, (end - start).days + 1)


def current_financial_year_window(today: date) -> tuple[date, date]:
    """Indian FY: 1 Apr - 31 Mar."""
    start_year = today.year if today.month >= 4 else today.year - 1
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


@dataclass
class MeAllocationRow:
    id: uuid.UUID
    engagement_code: str
    client_name: str
    role_on_engagement: str
    date_from: str
    date_to: str
    allocation_pct: float
    status: str
    work_location: str


def my_allocations(db: Session, staff_id: uuid.UUID, *, date_from: date, date_to: date) -> list[MeAllocationRow]:
    """Own bookings overlapping a window — default caller passes "today
    onward" for the mobile "what's next" view."""
    stmt = (
        select(Allocation, Engagement, Client)
        .join(Engagement, Allocation.engagement_id == Engagement.id)
        .join(Client, Engagement.client_id == Client.id)
        .where(Allocation.staff_id == staff_id)
        .where(Allocation.is_active == True)  # noqa: E712
        .where(Allocation.status != AllocationStatus.CANCELLED)
        .where(Allocation.date_from <= date_to.isoformat())
        .where(Allocation.date_to >= date_from.isoformat())
    )
    rows = []
    for alloc, engagement, client in db.exec(stmt).all():
        rows.append(
            MeAllocationRow(
                id=alloc.id, engagement_code=engagement.engagement_code, client_name=client.name,
                role_on_engagement=_plain(alloc.role_on_engagement), date_from=alloc.date_from, date_to=alloc.date_to,
                allocation_pct=alloc.allocation_pct, status=_plain(alloc.status), work_location=_plain(alloc.work_location),
            )
        )
    return sorted(rows, key=lambda r: r.date_from)


@dataclass
class MeLeaveBalance:
    financial_year_from: str
    financial_year_to: str
    entitlement_days: float | None
    approved_days_taken: float
    pending_days: float
    remaining_days: float | None


def my_leave_balance(db: Session, staff: Staff, *, as_of: date | None = None) -> MeLeaveBalance:
    """Approved-vs-entitlement for the current Indian FY (1 Apr - 31 Mar).
    Only leave types that count against entitlement are included (§3.9's
    `counts_against_entitlement` flag) — e.g. approved secondment or
    unpaid sabbatical wouldn't consume annual leave entitlement."""
    as_of = as_of or date.today()
    fy_from, fy_to = current_financial_year_window(as_of)

    stmt = (
        select(NonAvailability)
        .where(NonAvailability.staff_id == staff.id)
        .where(NonAvailability.is_active == True)  # noqa: E712
        .where(NonAvailability.counts_against_entitlement == True)  # noqa: E712
        .where(NonAvailability.date_from <= fy_to.isoformat())
        .where(NonAvailability.date_to >= fy_from.isoformat())
    )
    approved_days = pending_days = 0.0
    for lv in db.exec(stmt).all():
        days = _overlap_days(lv.date_from, lv.date_to, fy_from, fy_to)
        if lv.status == "APPROVED":
            approved_days += days
        elif lv.status == "APPLIED":
            pending_days += days

    entitlement = staff.leave_entitlement_days
    return MeLeaveBalance(
        financial_year_from=fy_from.isoformat(), financial_year_to=fy_to.isoformat(),
        entitlement_days=entitlement, approved_days_taken=approved_days, pending_days=pending_days,
        remaining_days=(entitlement - approved_days) if entitlement is not None else None,
    )


def my_recent_timesheets(db: Session, staff_id: uuid.UUID, *, days_back: int = 30, limit: int = 50) -> list[Timesheet]:
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    stmt = (
        select(Timesheet)
        .where(Timesheet.staff_id == staff_id)
        .where(Timesheet.is_active == True)  # noqa: E712
        .where(Timesheet.work_date >= cutoff)
        .order_by(Timesheet.work_date.desc())  # type: ignore[attr-defined]
        .limit(limit)
    )
    return list(db.exec(stmt).all())


def upcoming_confirmed_allocations_for_digest(db: Session, staff_id: uuid.UUID, *, window_from: date, window_to: date) -> list[MeAllocationRow]:
    """Just the CONFIRMED/IN_PROGRESS subset — what the weekly digest email
    (§9, notifications) actually cares about, not every draft/proposed idea."""
    stmt = (
        select(Allocation, Engagement, Client)
        .join(Engagement, Allocation.engagement_id == Engagement.id)
        .join(Client, Engagement.client_id == Client.id)
        .where(Allocation.staff_id == staff_id)
        .where(Allocation.is_active == True)  # noqa: E712
        .where(Allocation.status.in_([AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS]))  # type: ignore[attr-defined]
        .where(Allocation.date_from <= window_to.isoformat())
        .where(Allocation.date_to >= window_from.isoformat())
    )
    rows = []
    for alloc, engagement, client in db.exec(stmt).all():
        rows.append(
            MeAllocationRow(
                id=alloc.id, engagement_code=engagement.engagement_code, client_name=client.name,
                role_on_engagement=_plain(alloc.role_on_engagement), date_from=alloc.date_from, date_to=alloc.date_to,
                allocation_pct=alloc.allocation_pct, status=_plain(alloc.status), work_location=_plain(alloc.work_location),
            )
        )
    return sorted(rows, key=lambda r: r.date_from)
