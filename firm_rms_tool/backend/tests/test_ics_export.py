"""ICS calendar feed (P11) — both the pure builder and the /me/calendar.ics route."""
import datetime as dt
import uuid

from app.models.allocation import Allocation
from app.models.enums import AllocationRole, AllocationStatus, UserRole
from app.services.ics_export import build_ics_feed
from app.services.me_service import MeAllocationRow
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff


def test_build_ics_feed_has_valid_structure_and_folds_correctly():
    row = MeAllocationRow(
        id=uuid.uuid4(), engagement_code="STAT/CL-0001/2026-27-0001",
        client_name="A Very Long Client Name Private Limited That Should Trigger Line Folding In The Feed",
        role_on_engagement="TEAM_MEMBER", date_from="2026-09-01", date_to="2026-09-05",
        allocation_pct=100.0, status="CONFIRMED", work_location="CLIENT_SITE",
    )
    ics = build_ics_feed([row])

    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.rstrip("\r\n").endswith("END:VCALENDAR")
    assert "BEGIN:VEVENT" in ics and "END:VEVENT" in ics
    assert "DTSTART;VALUE=DATE:20260901" in ics
    assert "DTEND;VALUE=DATE:20260906" in ics  # exclusive end = date_to + 1 day
    assert f"UID:{row.id}@firm-rms" in ics
    # every physical line (before the trailing CRLF split) must be <= 75 chars,
    # except continuation lines which start with a space
    for line in ics.split("\r\n"):
        if line:
            assert len(line) <= 75, line


def test_build_ics_feed_empty_allocations_still_valid():
    ics = build_ics_feed([])
    assert "BEGIN:VCALENDAR" in ics
    assert "END:VCALENDAR" in ics
    assert "VEVENT" not in ics


def test_me_calendar_ics_route_returns_text_calendar(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session)
    make_user(session, UserRole.STAFF, email="ics1@x.com", staff_id=staff.id)
    headers = auth_headers(client, "ics1@x.com")

    today = dt.date.today()
    session.add(
        Allocation(
            engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
            date_from=today.isoformat(), date_to=(today + dt.timedelta(days=3)).isoformat(),
            allocation_pct=100, status=AllocationStatus.CONFIRMED,
        )
    )
    session.commit()

    resp = client.get("/api/v1/me/calendar.ics", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in resp.text
    assert engagement.engagement_code in resp.text


def test_me_calendar_ics_requires_linked_staff(client, session):
    make_user(session, UserRole.ADMIN, email="ics2@x.com")
    headers = auth_headers(client, "ics2@x.com")
    resp = client.get("/api/v1/me/calendar.ics", headers=headers)
    assert resp.status_code == 404
