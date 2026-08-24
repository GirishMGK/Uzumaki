"""Weekly booking digest email (§9 "scheduled emails", Phase P11).

Pure content-building is separated from the send loop so it's unit-testable
without SMTP: `build_digest_email` takes a staff row + their upcoming week's
CONFIRMED/IN_PROGRESS allocations and returns (subject, body); `send_weekly_digests`
does the DB read + loop + best-effort send (`app/services/notifications.py`
is already no-op-safe when SMTP isn't configured).
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from app.models.staff import Staff
from app.services.me_service import MeAllocationRow, upcoming_confirmed_allocations_for_digest
from app.services.notifications import send_notification


def build_digest_email(staff: Staff, allocations: list[MeAllocationRow], *, week_from: date, week_to: date) -> tuple[str, str]:
    subject = f"[firm-rms] Your bookings for {week_from.isoformat()} to {week_to.isoformat()}"
    if not allocations:
        body = f"Hi {staff.full_name},\n\nYou have no confirmed bookings for {week_from.isoformat()} to {week_to.isoformat()}."
        return subject, body

    lines = [f"Hi {staff.full_name},", "", f"Your confirmed bookings for {week_from.isoformat()} to {week_to.isoformat()}:", ""]
    for alloc in allocations:
        lines.append(
            f"- {alloc.date_from} to {alloc.date_to}: {alloc.client_name} ({alloc.engagement_code}), "
            f"{alloc.role_on_engagement}, {alloc.allocation_pct:.0f}%, {alloc.work_location}"
        )
    body = "\n".join(lines)
    return subject, body


def send_weekly_digests(db: Session, *, week_from: date | None = None, week_to: date | None = None) -> int:
    """Sends (or no-ops, if SMTP isn't configured) one digest per active
    staff member with an email on file. Returns the count of staff a
    digest was built for — not the count actually delivered, since
    delivery is intentionally best-effort (see notifications.py)."""
    week_from = week_from or date.today()
    week_to = week_to or (week_from + timedelta(days=6))

    staff_rows = db.exec(select(Staff).where(Staff.is_active == True)).all()  # noqa: E712
    sent = 0
    for staff in staff_rows:
        to_email = staff.official_email or staff.personal_email
        if not to_email:
            continue
        allocations = upcoming_confirmed_allocations_for_digest(db, staff.id, window_from=week_from, window_to=week_to)
        if not allocations:
            continue  # don't email an empty "nothing booked" digest every week
        subject, body = build_digest_email(staff, allocations, week_from=week_from, week_to=week_to)
        send_notification(to_email, subject, body)
        sent += 1
    return sent
