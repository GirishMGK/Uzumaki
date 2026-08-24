"""Minimal iCalendar (RFC 5545) generation for the `/me/calendar.ics` feed
(§10.2, Phase P11). Hand-rolled rather than adding a new dependency — the
same "no extra dependency for a well-understood text format" posture as
the client-side PNG chart export in P6.

Bookings are modelled as all-day VEVENTs (`date_from`/`date_to` on
`Allocation` are dates, not datetimes) — RFC 5545 all-day events use an
*exclusive* DTEND, so `date_to` is bumped by one day.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.me_service import MeAllocationRow

_CRLF = "\r\n"
_FOLD_WIDTH = 75


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 §3.1: a content line longer than 75 octets is split across
    multiple physical lines, each continuation starting with a space."""
    if len(line) <= _FOLD_WIDTH:
        return line
    chunks = [line[:_FOLD_WIDTH]]
    rest = line[_FOLD_WIDTH:]
    while rest:
        chunks.append(" " + rest[: _FOLD_WIDTH - 1])
        rest = rest[_FOLD_WIDTH - 1 :]
    return _CRLF.join(chunks)


def _vevent(alloc: MeAllocationRow, dtstamp: str) -> list[str]:
    start = datetime.strptime(alloc.date_from, "%Y-%m-%d").date()
    end_exclusive = datetime.strptime(alloc.date_to, "%Y-%m-%d").date() + timedelta(days=1)
    summary = f"{alloc.engagement_code} \u2014 {alloc.client_name} ({alloc.role_on_engagement})"
    description = (
        f"Role: {alloc.role_on_engagement}\\n"
        f"Allocation: {alloc.allocation_pct:.0f}%\\n"
        f"Status: {alloc.status}\\n"
        f"Location: {alloc.work_location}"
    )
    lines = [
        "BEGIN:VEVENT",
        f"UID:{alloc.id}@firm-rms",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{end_exclusive.strftime('%Y%m%d')}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        "STATUS:" + ("CONFIRMED" if alloc.status in ("CONFIRMED", "IN_PROGRESS", "COMPLETED") else "TENTATIVE"),
        "END:VEVENT",
    ]
    return [_fold(line) for line in lines]


def build_ics_feed(allocations: list[MeAllocationRow], *, calendar_name: str = "Firm RMS \u2014 My Bookings") -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//firm-rms//me-calendar//EN",
        "CALSCALE:GREGORIAN",
        _fold(f"X-WR-CALNAME:{_escape(calendar_name)}"),
    ]
    for alloc in allocations:
        lines.extend(_vevent(alloc, now))
    lines.append("END:VCALENDAR")
    return _CRLF.join(lines) + _CRLF
