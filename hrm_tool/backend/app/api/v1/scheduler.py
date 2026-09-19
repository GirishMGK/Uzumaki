"""Read-model endpoints purpose-built for the scheduler board (§6.1).

The board needs staff + their bookings + leave + holidays for a date
window in one round trip — assembling that from the generic
/staff, /allocations, /non-availability endpoints would mean N+1 client
side joins (allocation -> engagement -> client) for every bar rendered.
This module trades a bit of duplication for a single, board-shaped
response.
"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.deps import get_current_user, get_db
from app.models.allocation import Allocation, HolidayCalendar, NonAvailability
from app.models.client import Client
from app.models.engagement import Engagement
from app.models.staff import Staff
from app.models.user import User
from app.schemas.scheduler import (
    BoardAllocation,
    BoardHoliday,
    BoardLeave,
    BoardResponse,
    BoardStaff,
    EngagementLookupItem,
)

router = APIRouter()


@router.get("/board", response_model=BoardResponse)
def get_board(
    date_from: str,
    date_to: str,
    office_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    q: str | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BoardResponse:
    staff_stmt = select(Staff).where(Staff.is_active == True)  # noqa: E712
    if office_id:
        staff_stmt = staff_stmt.where(Staff.base_office_id == office_id)
    if department_id:
        staff_stmt = staff_stmt.where(Staff.primary_department_id == department_id)
    if q:
        staff_stmt = staff_stmt.where(Staff.full_name.contains(q))  # type: ignore[union-attr]
    staff_stmt = staff_stmt.order_by(Staff.grade_rank, Staff.full_name).limit(limit)
    staff_rows = list(db.exec(staff_stmt).all())
    staff_ids = [s.id for s in staff_rows]

    allocations_out: list[BoardAllocation] = []
    leaves_out: list[BoardLeave] = []
    if staff_ids:
        alloc_stmt = (
            select(Allocation, Engagement, Client)
            .join(Engagement, Allocation.engagement_id == Engagement.id)
            .join(Client, Engagement.client_id == Client.id)
            .where(Allocation.staff_id.in_(staff_ids))  # type: ignore[attr-defined]
            .where(Allocation.is_active == True)  # noqa: E712
            .where(Allocation.date_from <= date_to)
            .where(Allocation.date_to >= date_from)
        )
        for alloc, engagement, client in db.exec(alloc_stmt).all():
            allocations_out.append(
                BoardAllocation(
                    id=alloc.id, staff_id=alloc.staff_id, engagement_id=engagement.id,
                    engagement_code=engagement.engagement_code, client_name=client.name,
                    department_id=alloc.department_id,
                    role_on_engagement=alloc.role_on_engagement, date_from=alloc.date_from,
                    date_to=alloc.date_to, allocation_pct=alloc.allocation_pct, status=alloc.status,
                    booking_type=alloc.booking_type, version=alloc.version,
                )
            )

        leave_stmt = (
            select(NonAvailability)
            .where(NonAvailability.staff_id.in_(staff_ids))  # type: ignore[attr-defined]
            .where(NonAvailability.is_active == True)  # noqa: E712
            .where(NonAvailability.status.in_(["APPLIED", "APPROVED"]))  # type: ignore[attr-defined]
            .where(NonAvailability.date_from <= date_to)
            .where(NonAvailability.date_to >= date_from)
        )
        leaves_out = [
            BoardLeave(
                id=lv.id, staff_id=lv.staff_id, type=lv.type, date_from=lv.date_from,
                date_to=lv.date_to, day_fraction=lv.day_fraction, status=lv.status,
            )
            for lv in db.exec(leave_stmt).all()
        ]

    holiday_stmt = (
        select(HolidayCalendar)
        .where(HolidayCalendar.is_active == True)  # noqa: E712
        .where(HolidayCalendar.holiday_date >= date_from)
        .where(HolidayCalendar.holiday_date <= date_to)
    )
    if office_id:
        holiday_stmt = holiday_stmt.where((HolidayCalendar.office_id == office_id) | (HolidayCalendar.office_id.is_(None)))  # type: ignore[union-attr]
    holidays_out = [
        BoardHoliday(holiday_date=h.holiday_date, name=h.name, office_id=h.office_id)
        for h in db.exec(holiday_stmt).all()
    ]

    return BoardResponse(
        staff=[
            BoardStaff(
                id=s.id, employee_code=s.employee_code, full_name=s.full_name,
                staff_category=s.staff_category, designation=s.designation, grade_rank=s.grade_rank,
                base_office_id=s.base_office_id, primary_department_id=s.primary_department_id,
                standard_hours_per_week=s.standard_hours_per_week,
            )
            for s in staff_rows
        ],
        allocations=allocations_out,
        leaves=leaves_out,
        holidays=holidays_out,
    )


@router.get("/engagements-lookup", response_model=list[EngagementLookupItem])
def engagements_lookup(
    q: str | None = None,
    department_id: uuid.UUID | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[EngagementLookupItem]:
    stmt = (
        select(Engagement, Client)
        .join(Client, Engagement.client_id == Client.id)
        .where(Engagement.is_active == True)  # noqa: E712
    )
    if department_id:
        stmt = stmt.where(Engagement.department_id == department_id)
    if q:
        stmt = stmt.where((Engagement.engagement_code.contains(q)) | (Client.name.contains(q)))  # type: ignore[union-attr]
    stmt = stmt.limit(limit)
    return [
        EngagementLookupItem(
            id=e.id, engagement_code=e.engagement_code, client_name=c.name,
            department_id=e.department_id, status=e.status, eqcr_partner_id=e.eqcr_partner_id,
        )
        for e, c in db.exec(stmt).all()
    ]
