"""Chart data for the mandatory dashboards C1-C6 (§7.1).

Two families of query here:
- C1/C2 are pure headcount snapshots over `staff` — no date range, no
  allocations involved (T9's reconciliation check is exactly a raw
  `SELECT office, staff_category, COUNT(*)` on active staff).
- C3/C4/C5/C6 are FTE-over-a-period metrics. FTE for one allocation over
  the requested window is `(allocation_pct / 100) x (overlap_days /
  period_days)` — a person on two 50% bookings for the whole window
  contributes 0.5 FTE to each, never 1.0 to both (T10). `fetch_allocation_fte_rows`
  computes this once; every C3/C4/C5/C6 endpoint groups the same rows
  differently rather than re-deriving the FTE math per chart.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlmodel import Session, select

from app.models.allocation import Allocation
from app.models.client import Client, ClientGroup
from app.models.engagement import Engagement
from app.models.enums import AllocationStatus, BookingType, StaffCategory
from app.models.reference import Department, Office
from app.models.staff import Staff


@dataclass
class DashboardFilters:
    office_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    partner_id: uuid.UUID | None = None
    client_group_id: uuid.UUID | None = None
    staff_category: str | None = None


@dataclass
class FteRow:
    allocation_id: uuid.UUID
    staff_id: uuid.UUID
    staff_category: str
    designation: str
    grade_rank: int
    office_id: uuid.UUID | None
    department_id: uuid.UUID | None
    partner_id: uuid.UUID | None
    engagement_id: uuid.UUID
    client_id: uuid.UUID
    client_group_id: uuid.UUID | None
    risk_rating: str
    fee_amount: float | None
    fte: float
    staff_name: str = ""
    engagement_code: str = ""
    client_name: str = ""
    role_on_engagement: str = ""
    date_from: str = ""
    date_to: str = ""
    allocation_pct: float = 0


def _overlap_days(a_from: date, a_to: date, w_from: date, w_to: date) -> int:
    start = max(a_from, w_from)
    end = min(a_to, w_to)
    return max(0, (end - start).days + 1)


def _to_date(s: str) -> date:
    from datetime import datetime

    return datetime.strptime(s, "%Y-%m-%d").date()


def fetch_allocation_fte_rows(
    db: Session, date_from: date, date_to: date, filters: DashboardFilters | None = None
) -> list[FteRow]:
    filters = filters or DashboardFilters()
    period_days = (date_to - date_from).days + 1

    stmt = (
        select(Allocation, Staff, Engagement, Client)
        .join(Staff, Allocation.staff_id == Staff.id)
        .join(Engagement, Allocation.engagement_id == Engagement.id)
        .join(Client, Engagement.client_id == Client.id)
        .where(Allocation.is_active == True)  # noqa: E712
        .where(Allocation.status.in_([AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS]))  # type: ignore[attr-defined]
        .where(Allocation.booking_type == BookingType.HARD)
        .where(Allocation.date_from <= date_to.isoformat())
        .where(Allocation.date_to >= date_from.isoformat())
        .where(Staff.is_active == True)  # noqa: E712
    )
    if filters.office_id:
        stmt = stmt.where(Staff.base_office_id == filters.office_id)
    if filters.department_id:
        stmt = stmt.where(Engagement.department_id == filters.department_id)
    if filters.partner_id:
        stmt = stmt.where(Engagement.engagement_partner_id == filters.partner_id)
    if filters.client_group_id:
        stmt = stmt.where(Client.group_id == filters.client_group_id)
    if filters.staff_category:
        stmt = stmt.where(Staff.staff_category == filters.staff_category)

    rows: list[FteRow] = []
    for alloc, staff, engagement, client in db.exec(stmt).all():
        overlap = _overlap_days(_to_date(alloc.date_from), _to_date(alloc.date_to), date_from, date_to)
        if overlap <= 0:
            continue
        fte = (alloc.allocation_pct / 100) * (overlap / period_days)
        rows.append(
            FteRow(
                allocation_id=alloc.id, staff_id=staff.id, staff_category=staff.staff_category,
                designation=staff.designation, grade_rank=staff.grade_rank, office_id=staff.base_office_id,
                department_id=alloc.department_id or engagement.department_id,
                partner_id=engagement.engagement_partner_id, engagement_id=engagement.id, client_id=client.id,
                client_group_id=client.group_id, risk_rating=client.risk_rating, fee_amount=engagement.fee_amount,
                fte=round(fte, 4), staff_name=staff.full_name, engagement_code=engagement.engagement_code,
                client_name=client.name, role_on_engagement=alloc.role_on_engagement,
                date_from=alloc.date_from, date_to=alloc.date_to, allocation_pct=alloc.allocation_pct,
            )
        )
    return rows


# ---------------------------------------------------------------- C1 / C2 --

def headcount_by_office_category(db: Session, *, office_id: uuid.UUID | None = None) -> list[dict]:
    """C1: stacked bar, X=office, stacks=staff_category. T9-checkable."""
    stmt = select(Staff).where(Staff.is_active == True)  # noqa: E712
    if office_id:
        stmt = stmt.where(Staff.base_office_id == office_id)
    offices = {o.id: o for o in db.exec(select(Office)).all()}

    counts: dict[tuple, int] = defaultdict(int)
    for s in db.exec(stmt).all():
        counts[(s.base_office_id, s.staff_category)] += 1

    return [
        {
            "office_id": office_key,
            "office_name": offices[office_key].name if office_key in offices else "Unassigned",
            "staff_category": category,
            "count": count,
        }
        for (office_key, category), count in sorted(counts.items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))
    ]


def headcount_by_office_grade(db: Session, *, office_id: uuid.UUID | None = None) -> list[dict]:
    """C2: heatmap table, rows=office, cols=designation."""
    stmt = select(Staff).where(Staff.is_active == True)  # noqa: E712
    if office_id:
        stmt = stmt.where(Staff.base_office_id == office_id)
    offices = {o.id: o for o in db.exec(select(Office)).all()}

    counts: dict[tuple, int] = defaultdict(int)
    for s in db.exec(stmt).all():
        counts[(s.base_office_id, s.designation)] += 1

    return [
        {
            "office_id": office_key,
            "office_name": offices[office_key].name if office_key in offices else "Unassigned",
            "designation": designation,
            "count": count,
        }
        for (office_key, designation), count in sorted(counts.items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))
    ]


# --------------------------------------------------------------------- C3 --

def partner_wise_fte(db: Session, date_from: date, date_to: date, filters: DashboardFilters | None = None) -> list[dict]:
    """C3: horizontal stacked bar, Y=partner, stacks=grade (designation)."""
    rows = fetch_allocation_fte_rows(db, date_from, date_to, filters)
    partners = {p.id: p for p in db.exec(select(Staff)).all()}

    totals: dict[tuple, float] = defaultdict(float)
    for r in rows:
        if r.partner_id is None:
            continue
        totals[(r.partner_id, r.designation)] += r.fte

    return [
        {
            "partner_id": partner_id,
            "partner_name": partners[partner_id].full_name if partner_id in partners else "Unknown",
            "designation": designation,
            "fte": round(fte, 2),
        }
        for (partner_id, designation), fte in sorted(totals.items(), key=lambda kv: str(kv[0][0]))
    ]


# --------------------------------------------------------------------- C4 --

def partner_portfolio(db: Session, date_from: date, date_to: date, filters: DashboardFilters | None = None) -> list[dict]:
    """C4: bubble — X=fee under mgmt, Y=FTE deployed, size=engagement count, colour=avg risk."""
    rows = fetch_allocation_fte_rows(db, date_from, date_to, filters)
    partners = {p.id: p for p in db.exec(select(Staff)).all()}

    risk_score = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "SIGNIFICANT": 4}
    fte_by_partner: dict[uuid.UUID, float] = defaultdict(float)
    engagements_by_partner: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    fee_by_partner: dict[uuid.UUID, float] = defaultdict(float)
    risk_by_partner: dict[uuid.UUID, list[int]] = defaultdict(list)
    counted_engagements: set[tuple] = set()

    for r in rows:
        if r.partner_id is None:
            continue
        fte_by_partner[r.partner_id] += r.fte
        engagements_by_partner[r.partner_id].add(r.engagement_id)
        risk_by_partner[r.partner_id].append(risk_score.get(r.risk_rating, 2))
        key = (r.partner_id, r.engagement_id)
        if key not in counted_engagements and r.fee_amount:
            fee_by_partner[r.partner_id] += r.fee_amount
            counted_engagements.add(key)

    out = []
    for partner_id, fte in fte_by_partner.items():
        risks = risk_by_partner[partner_id]
        out.append(
            {
                "partner_id": partner_id,
                "partner_name": partners[partner_id].full_name if partner_id in partners else "Unknown",
                "fee_under_management": round(fee_by_partner.get(partner_id, 0), 2),
                "fte_deployed": round(fte, 2),
                "engagement_count": len(engagements_by_partner[partner_id]),
                "avg_risk_score": round(sum(risks) / len(risks), 2) if risks else 0,
            }
        )
    return sorted(out, key=lambda r: -r["fee_under_management"])


# --------------------------------------------------------------------- C5 --

def department_wise_fte(db: Session, date_from: date, date_to: date, filters: DashboardFilters | None = None) -> list[dict]:
    """C5 (current period slice): donut share of total allocated FTE by department."""
    rows = fetch_allocation_fte_rows(db, date_from, date_to, filters)
    departments = {d.id: d for d in db.exec(select(Department)).all()}

    totals: dict[uuid.UUID | None, float] = defaultdict(float)
    for r in rows:
        totals[r.department_id] += r.fte

    return [
        {
            "department_id": dept_id,
            "department_name": departments[dept_id].name if dept_id in departments else "Unassigned",
            "fte": round(fte, 2),
        }
        for dept_id, fte in sorted(totals.items(), key=lambda kv: -kv[1])
    ]


def department_wise_fte_trend(db: Session, months: int = 12, filters: DashboardFilters | None = None) -> list[dict]:
    """C5 trend line: FTE by department for each of the trailing `months` calendar months."""
    today = date.today()
    departments = {d.id: d for d in db.exec(select(Department)).all()}
    out = []
    # Walk back month by month using a simple 30-day-ish bucket boundary via replace().
    year, month = today.year, today.month
    buckets = []
    for _ in range(months):
        buckets.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    buckets.reverse()

    for (y, m) in buckets:
        m_from = date(y, m, 1)
        m_to = date(y + 1, 1, 1) - timedelta(days=1) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)
        rows = fetch_allocation_fte_rows(db, m_from, m_to, filters)
        totals: dict[uuid.UUID | None, float] = defaultdict(float)
        for r in rows:
            totals[r.department_id] += r.fte
        for dept_id, fte in totals.items():
            out.append(
                {
                    "month": m_from.strftime("%Y-%m"),
                    "department_id": dept_id,
                    "department_name": departments[dept_id].name if dept_id in departments else "Unassigned",
                    "fte": round(fte, 2),
                }
            )
    return out


# --------------------------------------------------------------------- C6 --

def department_by_grade(db: Session, date_from: date, date_to: date, filters: DashboardFilters | None = None) -> list[dict]:
    """C6: grouped bar, X=department, groups=grade (designation)."""
    rows = fetch_allocation_fte_rows(db, date_from, date_to, filters)
    departments = {d.id: d for d in db.exec(select(Department)).all()}

    totals: dict[tuple, float] = defaultdict(float)
    for r in rows:
        totals[(r.department_id, r.designation)] += r.fte

    return [
        {
            "department_id": dept_id,
            "department_name": departments[dept_id].name if dept_id in departments else "Unassigned",
            "designation": designation,
            "fte": round(fte, 2),
        }
        for (dept_id, designation), fte in sorted(totals.items(), key=lambda kv: str(kv[0][0]))
    ]
