"""Engagement roll-forward (§8/§9, Phase P10) — `Engagement.prior_year_engagement_id`
(§3.5) exists precisely for this: cloning an engagement into the next
financial year without re-typing its whole setup by hand.

Financial terms (fee, OOP budget, billing milestones), UDIN and the report
sign-off date are deliberately **not** copied — those are renegotiated /
re-earned each year, and carrying them over silently would misstate a
brand-new engagement as already billed or already reported. Team
continuity (partner/EQCR/manager) and delivery configuration (budget
hours, required skills, priority/complexity) *are* copied, since that's
the actual point of a roll-forward: don't make the RM rebuild the team
from scratch.

Team allocations are copied one line at a time through the real conflict
engine (R1-R24) with dates shifted a year — a line with any BLOCK or WARN
is skipped and reported rather than silently forced through, since a bulk
operation has no human standing by to type an override reason (§4).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.models.allocation import Allocation
from app.models.enums import AllocationStatus, AuditAction
from app.models.engagement import Engagement
from app.models.staff import Staff
from app.services.capacity_materializer import recompute_range
from app.services.conflict_engine import AllocationCandidate, validate_allocation

_FY_RE = re.compile(r"^FY(\d{4})-(\d{2})$")

# Statuses that mean "actually delivering" — DRAFT/PROPOSED/CANCELLED never
# represented real team commitment, so they're not worth rolling forward.
DELIVERED_STATUSES = (AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS, AllocationStatus.COMPLETED)

# Fields carried forward as-is: team/role continuity + delivery configuration.
_COPIED_FIELDS = (
    "client_id", "department_id", "service_type", "period_type",
    "signing_partner_id", "engagement_partner_id", "eqcr_partner_id", "eqcr_required", "engagement_manager_id",
    "priority", "complexity", "budget_hours_total", "budget_hours_by_grade",
    "first_year_of_appointment", "rotation_due_fy", "ep_rotation_due_fy", "requires_specialist_skills",
)
# Date fields shifted by the same year delta as the engagement itself, if set.
_SHIFTED_DATE_FIELDS = (
    "period_from", "period_to", "reporting_deadline", "statutory_due_date",
    "internal_target_date", "planned_start", "planned_end", "appointment_date",
)


def shift_date(s: str, years: int) -> str:
    d = datetime.strptime(s, "%Y-%m-%d").date()
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:
        # Feb 29 rolling into a non-leap year.
        return d.replace(year=d.year + years, day=28).isoformat()


def shift_financial_year(fy: str, years: int) -> str:
    m = _FY_RE.match(fy)
    if not m:
        return fy  # unrecognised format — leave untouched rather than guess
    start = int(m.group(1)) + years
    return f"FY{start}-{(start + 1) % 100:02d}"


@dataclass
class RollForwardResult:
    new_engagement: Engagement
    copied: list[dict]
    skipped: list[dict]


def roll_forward_engagement(
    db: Session,
    source: Engagement,
    *,
    new_engagement_code: str,
    new_financial_year: str | None,
    date_shift_years: int,
    copy_team: bool,
    actor_id: uuid.UUID | None,
    ip: str | None,
    user_agent: str | None,
) -> RollForwardResult:
    new_engagement = Engagement(
        engagement_code=new_engagement_code,
        financial_year=new_financial_year or shift_financial_year(source.financial_year, date_shift_years),
        status="PIPELINE",
        prior_year_engagement_id=source.id,
        created_by=actor_id, updated_by=actor_id,
        **{f: getattr(source, f) for f in _COPIED_FIELDS},
        **{f: (shift_date(v, date_shift_years) if (v := getattr(source, f)) else None) for f in _SHIFTED_DATE_FIELDS},
    )
    db.add(new_engagement)
    db.flush()
    write_audit_log(
        db, entity_type="engagements", entity_id=new_engagement.id, action=AuditAction.CREATE, actor_id=actor_id,
        before=None, after=new_engagement, ip=ip, user_agent=user_agent,
    )

    copied: list[dict] = []
    skipped: list[dict] = []
    affected_staff_ids: set[uuid.UUID] = set()

    if copy_team:
        source_allocs = db.exec(
            select(Allocation)
            .where(Allocation.engagement_id == source.id)
            .where(Allocation.is_active == True)  # noqa: E712
            .where(Allocation.status.in_(DELIVERED_STATUSES))  # type: ignore[attr-defined]
        ).all()
        staff_by_id = {s.id: s for s in db.exec(select(Staff)).all()}

        for alloc in source_allocs:
            new_from = shift_date(alloc.date_from, date_shift_years)
            new_to = shift_date(alloc.date_to, date_shift_years)
            # Check as if this line were CONFIRMED (so R1 overallocation etc. actually
            # fire — it's gated on CONFIRMED/IN_PROGRESS status by design, §4 R1), even
            # though the row that actually gets written stays DRAFT until an RM reviews
            # it — a real double-booking should surface now, not silently at confirm time.
            cand = AllocationCandidate(
                engagement_id=new_engagement.id, staff_id=alloc.staff_id, role_on_engagement=alloc.role_on_engagement,
                date_from=new_from, date_to=new_to, allocation_pct=alloc.allocation_pct, status=AllocationStatus.CONFIRMED,
            )
            violations = validate_allocation(db, cand)
            blocking_or_warn = [v for v in violations if v.severity in ("BLOCK", "WARN")]
            staff = staff_by_id.get(alloc.staff_id)
            if blocking_or_warn:
                skipped.append(
                    {
                        "staff_id": str(alloc.staff_id), "staff_name": staff.full_name if staff else "",
                        "role_on_engagement": alloc.role_on_engagement, "date_from": new_from, "date_to": new_to,
                        "reasons": [f"{v.code}: {v.message}" for v in blocking_or_warn],
                    }
                )
                continue
            new_row = Allocation(
                engagement_id=new_engagement.id, staff_id=alloc.staff_id, role_on_engagement=alloc.role_on_engagement,
                date_from=new_from, date_to=new_to, allocation_pct=alloc.allocation_pct, status=AllocationStatus.DRAFT,
                is_chargeable=alloc.is_chargeable, work_location=alloc.work_location, office_id=alloc.office_id,
                notes=f"Rolled forward from {source.engagement_code}",
                requested_by=actor_id, created_by=actor_id, updated_by=actor_id,
            )
            db.add(new_row)
            db.flush()
            write_audit_log(
                db, entity_type="allocations", entity_id=new_row.id, action=AuditAction.CREATE, actor_id=actor_id,
                before=None, after=new_row, ip=ip, user_agent=user_agent,
            )
            copied.append(
                {
                    "staff_id": str(alloc.staff_id), "staff_name": staff.full_name if staff else "",
                    "role_on_engagement": alloc.role_on_engagement, "date_from": new_from, "date_to": new_to,
                }
            )
            affected_staff_ids.add(alloc.staff_id)

    db.commit()

    if affected_staff_ids:
        all_dates = [datetime.strptime(c["date_from"], "%Y-%m-%d").date() for c in copied] + [
            datetime.strptime(c["date_to"], "%Y-%m-%d").date() for c in copied
        ]
        # recompute_range commits internally, which expires every object in this
        # session (incl. new_engagement) — refresh it again afterward, not before.
        recompute_range(db, min(all_dates) - timedelta(days=1), max(all_dates) + timedelta(days=1), staff_ids=list(affected_staff_ids))

    db.refresh(new_engagement)
    return RollForwardResult(new_engagement=new_engagement, copied=copied, skipped=skipped)
