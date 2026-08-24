"""Acceptance test T8 (§14)."""
from app.models.allocation import HolidayCalendar, NonAvailability
from app.services.capacity import compute_net_capacity_hours
from tests.factories import make_staff


def test_t8_net_capacity_with_leave_and_holiday(session):
    staff = make_staff(session, standard_hours_per_week=40)

    # Mon 2026-09-07 .. Fri 2026-09-11 is a 5-day working week.
    session.add(NonAvailability(staff_id=staff.id, type="PRIVILEGE_LEAVE", date_from="2026-09-07", date_to="2026-09-08", status="APPROVED"))
    session.add(HolidayCalendar(office_id=None, holiday_date="2026-09-09", name="Ganesh Chaturthi"))
    session.commit()

    net_hours = compute_net_capacity_hours(session, staff.id, "2026-09-07", "2026-09-11")
    assert net_hours == 16.0
