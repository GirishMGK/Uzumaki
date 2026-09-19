"""Weekly digest email content + send loop (P11)."""
import datetime as dt

from app.models.allocation import Allocation
from app.models.enums import AllocationRole, AllocationStatus
from app.services.digest import build_digest_email, send_weekly_digests
from app.services.me_service import MeAllocationRow
from tests.factories import make_client, make_department, make_engagement, make_staff


def test_build_digest_email_empty_week():
    staff = type("S", (), {"full_name": "Aarav Roy"})()
    subject, body = build_digest_email(staff, [], week_from=dt.date(2026, 9, 7), week_to=dt.date(2026, 9, 13))
    assert "2026-09-07" in subject
    assert "no confirmed bookings" in body


def test_build_digest_email_lists_bookings():
    staff = type("S", (), {"full_name": "Aarav Roy"})()
    row = MeAllocationRow(
        id="x", engagement_code="STAT/CL-0001", client_name="Jain Pvt Ltd", role_on_engagement="TEAM_MEMBER",
        date_from="2026-09-08", date_to="2026-09-10", allocation_pct=100.0, status="CONFIRMED", work_location="OFFICE",
    )
    subject, body = build_digest_email(staff, [row], week_from=dt.date(2026, 9, 7), week_to=dt.date(2026, 9, 13))
    assert "Jain Pvt Ltd" in body
    assert "STAT/CL-0001" in body
    assert "100%" in body


def test_send_weekly_digests_only_counts_staff_with_confirmed_bookings(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    booked_staff = make_staff(session, official_email="booked@firm.local")
    idle_staff = make_staff(session, official_email="idle@firm.local")
    no_email_staff = make_staff(session, official_email=None, personal_email=None)

    week_from = dt.date(2026, 9, 7)
    session.add(
        Allocation(
            engagement_id=engagement.id, staff_id=booked_staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
            date_from=week_from.isoformat(), date_to=(week_from + dt.timedelta(days=2)).isoformat(),
            allocation_pct=100, status=AllocationStatus.CONFIRMED,
        )
    )
    # a DRAFT booking for no_email_staff — shouldn't matter anyway since they have no email
    session.add(
        Allocation(
            engagement_id=engagement.id, staff_id=no_email_staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
            date_from=week_from.isoformat(), date_to=(week_from + dt.timedelta(days=2)).isoformat(),
            allocation_pct=100, status=AllocationStatus.CONFIRMED,
        )
    )
    session.commit()

    count = send_weekly_digests(session, week_from=week_from, week_to=week_from + dt.timedelta(days=6))
    assert count == 1  # only booked_staff: has an email AND a confirmed booking in the window
