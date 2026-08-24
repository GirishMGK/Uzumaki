"""Actuals + margin (§9, Phase P9).

Only APPROVED timesheets ever count as an "actual" — DRAFT/SUBMITTED/
REJECTED rows are work in progress or disputed, not evidence yet. That
mirrors the rest of this build's evidence posture (§0.1): a number here
means someone with approval authority signed off on it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlmodel import Session, select

from app.models.allocation import Timesheet
from app.models.engagement import Engagement
from app.models.enums import TimesheetStatus
from app.models.staff import Staff


@dataclass
class ActualsSummary:
    actual_hours: float = 0.0
    actual_chargeable_hours: float = 0.0
    actual_cost: float = 0.0
    entries: int = 0


def _approved_timesheets(
    db: Session, *, staff_id: uuid.UUID | None = None, engagement_id: uuid.UUID | None = None,
    date_from: date | None = None, date_to: date | None = None,
) -> list[Timesheet]:
    stmt = select(Timesheet).where(Timesheet.is_active == True).where(Timesheet.status == TimesheetStatus.APPROVED.value)  # noqa: E712
    if staff_id:
        stmt = stmt.where(Timesheet.staff_id == staff_id)
    if engagement_id:
        stmt = stmt.where(Timesheet.engagement_id == engagement_id)
    if date_from:
        stmt = stmt.where(Timesheet.work_date >= date_from.isoformat())
    if date_to:
        stmt = stmt.where(Timesheet.work_date <= date_to.isoformat())
    return list(db.exec(stmt).all())


def engagement_actuals(
    db: Session, engagement_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None,
) -> ActualsSummary:
    """Actual hours + cost booked against one engagement, from APPROVED timesheets."""
    rows = _approved_timesheets(db, engagement_id=engagement_id, date_from=date_from, date_to=date_to)
    if not rows:
        return ActualsSummary()
    staff_ids = {r.staff_id for r in rows}
    cost_rate_by_staff = {
        s.id: s.cost_rate_per_hour or 0.0 for s in db.exec(select(Staff).where(Staff.id.in_(staff_ids))).all()  # type: ignore[attr-defined]
    }
    summary = ActualsSummary(entries=len(rows))
    for r in rows:
        summary.actual_hours += r.hours
        if r.is_chargeable:
            summary.actual_chargeable_hours += r.hours
        summary.actual_cost += r.hours * cost_rate_by_staff.get(r.staff_id, 0.0)
    return summary


def staff_actuals(
    db: Session, staff_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None,
) -> ActualsSummary:
    """Actual hours booked by one staff member across all engagements, from APPROVED timesheets."""
    rows = _approved_timesheets(db, staff_id=staff_id, date_from=date_from, date_to=date_to)
    staff = db.get(Staff, staff_id)
    cost_rate = (staff.cost_rate_per_hour or 0.0) if staff else 0.0
    summary = ActualsSummary(entries=len(rows))
    for r in rows:
        summary.actual_hours += r.hours
        if r.is_chargeable:
            summary.actual_chargeable_hours += r.hours
        summary.actual_cost += r.hours * cost_rate
    return summary


@dataclass
class EngagementMargin:
    engagement_id: uuid.UUID
    fee_amount: float | None
    actual_cost: float
    out_of_pocket_budget: float | None
    margin_amount: float | None
    margin_pct: float | None
    budget_hours_total: float | None
    actual_hours: float
    hours_variance_pct: float | None  # (actual - budget) / budget * 100; positive = over budget


def engagement_margin(db: Session, engagement_id: uuid.UUID) -> EngagementMargin | None:
    """Realised margin to date: fee less actual staff cost (from APPROVED timesheets)
    less the out-of-pocket budget (no actual-OOP tracking exists yet, so the budget
    figure is used as a conservative proxy — see docs/decisions.md)."""
    engagement = db.get(Engagement, engagement_id)
    if engagement is None:
        return None
    actuals = engagement_actuals(db, engagement_id)

    margin_amount = margin_pct = None
    if engagement.fee_amount is not None:
        margin_amount = engagement.fee_amount - actuals.actual_cost - (engagement.out_of_pocket_budget or 0.0)
        if engagement.fee_amount:
            margin_pct = round(margin_amount / engagement.fee_amount * 100, 1)

    hours_variance_pct = None
    if engagement.budget_hours_total:
        hours_variance_pct = round((actuals.actual_hours - engagement.budget_hours_total) / engagement.budget_hours_total * 100, 1)

    return EngagementMargin(
        engagement_id=engagement_id,
        fee_amount=engagement.fee_amount,
        actual_cost=round(actuals.actual_cost, 2),
        out_of_pocket_budget=engagement.out_of_pocket_budget,
        margin_amount=round(margin_amount, 2) if margin_amount is not None else None,
        margin_pct=margin_pct,
        budget_hours_total=engagement.budget_hours_total,
        actual_hours=round(actuals.actual_hours, 2),
        hours_variance_pct=hours_variance_pct,
    )
