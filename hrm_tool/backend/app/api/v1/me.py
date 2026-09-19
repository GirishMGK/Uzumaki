"""Self-service "mobile /me" endpoints (§10.2, Phase P11).

Every route here is scoped to `Depends(get_current_user)`'s own
`staff_id` — there is no way to ask for anyone else's data through this
router, by construction (no `staff_id` query/path parameter exists on any
route below). A login with no linked `staff_id` (a pure system/admin
account, §3.6/decisions.md) gets a 404 rather than an empty profile, since
"no staff record" and "staff record with nothing in it" are different
things worth distinguishing.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import Session

from app.core.deps import can_see_financials, get_current_user, get_db
from app.models.staff import Staff
from app.models.user import User
from app.schemas.me import MeAllocationOut, MeLeaveBalanceOut, MeProfileOut
from app.schemas.staff import StaffRead
from app.schemas.timesheet import TimesheetRead
from app.services.ics_export import build_ics_feed
from app.services.me_service import my_allocations, my_leave_balance, my_recent_timesheets

router = APIRouter()

DEFAULT_LOOKAHEAD_DAYS = 60
# A wider window than the JSON view (§10.2) — a subscribed calendar should
# show what's already on the books, not just what's coming up next.
ICS_LOOKBACK_DAYS = 30
ICS_LOOKAHEAD_DAYS = 365


def _require_staff(db: Session, user: User) -> Staff:
    if user.staff_id is None:
        raise HTTPException(status_code=404, detail="This login has no linked staff record")
    staff = db.get(Staff, user.staff_id)
    if staff is None or not staff.is_active:
        raise HTTPException(status_code=404, detail="This login has no linked staff record")
    return staff


@router.get("", response_model=MeProfileOut)
def get_my_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> MeProfileOut:
    staff = None
    if user.staff_id is not None:
        row = db.get(Staff, user.staff_id)
        if row is not None and row.is_active:
            staff = StaffRead.from_orm_masked(row, mask_financials=not can_see_financials(user))
    return MeProfileOut(user_id=user.id, email=user.email, role=user.role, full_name=user.full_name, staff=staff)


@router.get("/allocations", response_model=list[MeAllocationOut])
def get_my_allocations(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[MeAllocationOut]:
    staff = _require_staff(db, user)
    d_from = date_from or date.today()
    d_to = date_to or (d_from + timedelta(days=DEFAULT_LOOKAHEAD_DAYS))
    rows = my_allocations(db, staff.id, date_from=d_from, date_to=d_to)
    return [MeAllocationOut(**r.__dict__) for r in rows]


@router.get("/leave-balance", response_model=MeLeaveBalanceOut)
def get_my_leave_balance(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> MeLeaveBalanceOut:
    staff = _require_staff(db, user)
    balance = my_leave_balance(db, staff)
    return MeLeaveBalanceOut(**balance.__dict__)


@router.get("/timesheets", response_model=list[TimesheetRead])
def get_my_timesheets(
    days_back: int = 30, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> list[TimesheetRead]:
    staff = _require_staff(db, user)
    return list(my_recent_timesheets(db, staff.id, days_back=days_back))


@router.get("/calendar.ics")
def get_my_calendar_ics(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    """A subscribable calendar of the caller's own bookings. Note this is
    JWT-authenticated like the rest of the API, which real calendar apps
    (Google/Apple/Outlook Calendar "subscribe by URL") can't do — they send
    no Bearer header. A production deployment would need a separate
    long-lived per-user feed token for this one endpoint; see
    docs/decisions.md. This build keeps the same auth as everything else
    and is meant to be pulled by a client that *can* attach the header
    (a script, the `/me` mobile page's "download" action, `curl`), not
    subscribed to directly from a calendar app yet.
    """
    staff = _require_staff(db, user)
    window_from = date.today() - timedelta(days=ICS_LOOKBACK_DAYS)
    window_to = date.today() + timedelta(days=ICS_LOOKAHEAD_DAYS)
    rows = my_allocations(db, staff.id, date_from=window_from, date_to=window_to)
    ics_text = build_ics_feed(rows, calendar_name=f"Firm RMS — {staff.full_name}")
    return Response(content=ics_text, media_type="text/calendar; charset=utf-8")
