"""API coverage for /me — self-service profile, allocations, leave balance,
timesheets (P11)."""
import datetime as dt

from app.models.allocation import Allocation, NonAvailability
from app.models.enums import AllocationRole, AllocationStatus, UserRole
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff


def _setup(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session, leave_entitlement_days=18)
    return dept, cl, engagement, staff


def test_me_profile_for_linked_staff(client, session):
    _, _, _, staff = _setup(session)
    make_user(session, UserRole.STAFF, email="me1@x.com", staff_id=staff.id)
    headers = auth_headers(client, "me1@x.com")

    resp = client.get("/api/v1/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "me1@x.com"
    assert body["staff"]["employee_code"] == staff.employee_code


def test_me_profile_404_without_linked_staff(client, session):
    make_user(session, UserRole.ADMIN, email="me2@x.com")
    headers = auth_headers(client, "me2@x.com")

    resp = client.get("/api/v1/me/leave-balance", headers=headers)
    assert resp.status_code == 404


def test_me_allocations_within_window(client, session):
    _, _, engagement, staff = _setup(session)
    make_user(session, UserRole.STAFF, email="me3@x.com", staff_id=staff.id)
    headers = auth_headers(client, "me3@x.com")

    today = dt.date.today()
    in_window = Allocation(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from=(today + dt.timedelta(days=5)).isoformat(), date_to=(today + dt.timedelta(days=10)).isoformat(),
        allocation_pct=100, status=AllocationStatus.CONFIRMED,
    )
    far_future = Allocation(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from=(today + dt.timedelta(days=200)).isoformat(), date_to=(today + dt.timedelta(days=205)).isoformat(),
        allocation_pct=100, status=AllocationStatus.CONFIRMED,
    )
    session.add(in_window)
    session.add(far_future)
    session.commit()

    resp = client.get("/api/v1/me/allocations", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    codes = {r["engagement_code"] for r in rows}
    assert engagement.engagement_code in codes
    assert len(rows) == 1  # far_future is outside the default 60-day lookahead


def test_me_leave_balance_counts_approved_not_pending(client, session):
    _, _, _, staff = _setup(session)
    make_user(session, UserRole.STAFF, email="me4@x.com", staff_id=staff.id)
    headers = auth_headers(client, "me4@x.com")

    today = dt.date.today()
    fy_from = dt.date(today.year if today.month >= 4 else today.year - 1, 4, 5)
    approved = NonAvailability(
        staff_id=staff.id, type="PRIVILEGE_LEAVE", date_from=fy_from.isoformat(),
        date_to=(fy_from + dt.timedelta(days=4)).isoformat(), status="APPROVED", counts_against_entitlement=True,
    )
    pending = NonAvailability(
        staff_id=staff.id, type="CASUAL_LEAVE", date_from=(fy_from + dt.timedelta(days=10)).isoformat(),
        date_to=(fy_from + dt.timedelta(days=11)).isoformat(), status="APPLIED", counts_against_entitlement=True,
    )
    session.add(approved)
    session.add(pending)
    session.commit()

    resp = client.get("/api/v1/me/leave-balance", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["entitlement_days"] == 18
    assert body["approved_days_taken"] == 5
    assert body["pending_days"] == 2
    assert body["remaining_days"] == 13


def test_me_timesheets_scoped_to_self(client, session):
    from app.models.allocation import Timesheet

    _, _, engagement, staff = _setup(session)
    other_staff = make_staff(session)
    make_user(session, UserRole.STAFF, email="me5@x.com", staff_id=staff.id)
    headers = auth_headers(client, "me5@x.com")

    today = dt.date.today().isoformat()
    session.add(Timesheet(staff_id=staff.id, engagement_id=engagement.id, work_date=today, hours=8, status="DRAFT"))
    session.add(Timesheet(staff_id=other_staff.id, engagement_id=engagement.id, work_date=today, hours=8, status="DRAFT"))
    session.commit()

    resp = client.get("/api/v1/me/timesheets", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["staff_id"] == str(staff.id)
