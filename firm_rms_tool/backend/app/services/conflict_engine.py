"""The conflict / business-rules engine (§4).

`validate_allocation` runs the full rule set for a proposed (or edited)
allocation and returns a list of `RuleViolation`s without persisting
anything. Callers decide what to do with the result:

- any BLOCK severity -> the caller must refuse to save
- WARN severity -> caller may save only if the request carries a typed
  override reason, which the router mirrors into `allocations.override_flags`
  and `audit_log`
- INFO -> always safe to save; shown to the user for awareness only

This module implements the full rule set, R1-R24 (§4). R1-R9 shipped in
Phase P3; R10-R24 are Phase P8. A few of the P8 rules approximate spec
intent against fields the schema doesn't model exactly as named in §4 —
each of those has a docstring note plus a matching entry in
docs/decisions.md.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from sqlmodel import Session, select

from app.core.app_config import get_config_value
from app.core.config import get_settings
from app.models.allocation import Allocation, NonAvailability
from app.models.client import Client
from app.models.engagement import Engagement
from app.models.enums import (
    AcceptanceStatus,
    AllocationRole,
    AllocationStatus,
    EngagementStatus,
    NonAvailabilityStatus,
    NonAvailabilityType,
    StaffCategory,
)
from app.models.enums import QUALIFIED_SUPERVISOR_MAX_GRADE_RANK
from app.models.reference import Skill, StaffSkill
from app.models.staff import Staff

ARTICLE_CATEGORIES = (StaffCategory.ARTICLED_ASSISTANT, StaffCategory.INDUSTRIAL_TRAINEE)
# Roles a given engagement may only have one active holder of at a time (R23).
SINGLETON_ROLES = {
    AllocationRole.SIGNING_PARTNER,
    AllocationRole.ENGAGEMENT_PARTNER,
    AllocationRole.EQCR,
    AllocationRole.ENGAGEMENT_MANAGER,
}
ARTICLE_WEEKLY_HOURS_NORM = 35.0  # §5 — 35 hrs/week for articles vs 45 for employees
SUSTAINED_OVERLOAD_THRESHOLD_PCT = 90.0
# The schema stores notice_period_end but not notice_start (see docs/decisions.md);
# R21 approximates "final 20% of notice period" against a standard notice length.
STANDARD_NOTICE_PERIOD_DAYS = 30


@dataclass
class RuleViolation:
    code: str
    severity: Literal["BLOCK", "WARN", "INFO"]
    message: str
    context: dict = field(default_factory=dict)
    overridable: bool = False
    override_role: str | None = None


@dataclass
class AllocationCandidate:
    """Shape of a not-yet-persisted allocation, as posted to /validate."""

    engagement_id: uuid.UUID
    staff_id: uuid.UUID
    role_on_engagement: AllocationRole
    date_from: str
    date_to: str
    allocation_pct: float = 100
    status: AllocationStatus = AllocationStatus.CONFIRMED
    exclude_allocation_id: uuid.UUID | None = None  # when editing an existing row
    office_id: uuid.UUID | None = None  # feeds R16/R17; optional for backward compat
    work_location: str | None = None


def _to_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date() if isinstance(s, str) else s


def _week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso[0], iso[1])


def _business_days(d1: date, d2: date) -> int:
    return sum(1 for day in _daterange(d1, d2) if day.weekday() < 5)


def _daterange(d1: date, d2: date):
    for n in range((d2 - d1).days + 1):
        yield d1 + timedelta(n)


def _overlapping_active_allocations(
    db: Session, staff_id: uuid.UUID, date_from: str, date_to: str, exclude_id: uuid.UUID | None
) -> list[Allocation]:
    stmt = (
        select(Allocation)
        .where(Allocation.staff_id == staff_id)
        .where(Allocation.is_active == True)  # noqa: E712
        .where(Allocation.status.in_([AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS]))  # type: ignore[attr-defined]
        .where(Allocation.date_from <= date_to)
        .where(Allocation.date_to >= date_from)
    )
    rows = list(db.exec(stmt).all())
    if exclude_id:
        rows = [r for r in rows if r.id != exclude_id]
    return rows


def _overlapping_engagement_allocations(
    db: Session, engagement_id: uuid.UUID, date_from: str, date_to: str, exclude_id: uuid.UUID | None
) -> list[Allocation]:
    """Every staff member's active allocations on one engagement, overlapping a range."""
    stmt = (
        select(Allocation)
        .where(Allocation.engagement_id == engagement_id)
        .where(Allocation.is_active == True)  # noqa: E712
        .where(Allocation.status.in_([AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS, AllocationStatus.PROPOSED]))  # type: ignore[attr-defined]
        .where(Allocation.date_from <= date_to)
        .where(Allocation.date_to >= date_from)
    )
    rows = list(db.exec(stmt).all())
    if exclude_id:
        rows = [r for r in rows if r.id != exclude_id]
    return rows


def check_overallocation(db: Session, cand: AllocationCandidate) -> RuleViolation | None:
    """R1: sum of allocation_pct for a staff member on any day > 100%."""
    if cand.status not in (AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS):
        return None
    existing = _overlapping_active_allocations(db, cand.staff_id, cand.date_from, cand.date_to, cand.exclude_allocation_id)
    if not existing:
        return None
    d_from, d_to = _to_date(cand.date_from), _to_date(cand.date_to)
    worst_day, worst_pct = None, 0.0
    for day in _daterange(d_from, d_to):
        total = cand.allocation_pct
        for alloc in existing:
            if _to_date(alloc.date_from) <= day <= _to_date(alloc.date_to):
                total += alloc.allocation_pct
        if total > worst_pct:
            worst_pct, worst_day = total, day
    if worst_pct > 100:
        return RuleViolation(
            code="OVERALLOCATION",
            severity="BLOCK",
            message=f"Staff would be allocated {worst_pct:.0f}% on {worst_day.isoformat()}, exceeding 100%.",
            context={"date": worst_day.isoformat(), "total_pct": worst_pct},
            overridable=False,
        )
    return None


def check_leave_conflict(db: Session, cand: AllocationCandidate) -> RuleViolation | None:
    """R2: booking overlaps an APPROVED non_availability record."""
    stmt = (
        select(NonAvailability)
        .where(NonAvailability.staff_id == cand.staff_id)
        .where(NonAvailability.is_active == True)  # noqa: E712
        .where(NonAvailability.status == NonAvailabilityStatus.APPROVED.value)
        .where(NonAvailability.date_from <= cand.date_to)
        .where(NonAvailability.date_to >= cand.date_from)
    )
    leave = db.exec(stmt).first()
    if leave:
        return RuleViolation(
            code="LEAVE_CONFLICT",
            severity="BLOCK",
            message=f"Overlaps approved {leave.type} from {leave.date_from} to {leave.date_to}.",
            context={"non_availability_id": str(leave.id), "type": leave.type},
            overridable=False,
        )
    return None


def check_exam_leave(db: Session, cand: AllocationCandidate, staff: Staff) -> RuleViolation | None:
    """R3: article booked inside a declared exam_leave_block."""
    if not staff.exam_leave_blocks:
        return None
    d_from, d_to = _to_date(cand.date_from), _to_date(cand.date_to)
    for block in staff.exam_leave_blocks:
        b_from, b_to = _to_date(block["from"]), _to_date(block["to"])
        if b_from <= d_to and b_to >= d_from:
            return RuleViolation(
                code="EXAM_LEAVE",
                severity="BLOCK",
                message=f"Overlaps declared exam leave ({block.get('exam', 'exam')}) {block['from']} to {block['to']}.",
                context={"exam": block.get("exam"), "from": block["from"], "to": block["to"]},
                overridable=False,
            )
    return None


def check_not_joined_or_exited(cand: AllocationCandidate, staff: Staff) -> RuleViolation | None:
    """R4: booking outside employment window."""
    d_from, d_to = _to_date(cand.date_from), _to_date(cand.date_to)
    if staff.date_of_joining and d_from < _to_date(staff.date_of_joining):
        return RuleViolation(
            code="NOT_JOINED_OR_EXITED",
            severity="BLOCK",
            message=f"Booking starts before joining date {staff.date_of_joining}.",
            context={"date_of_joining": staff.date_of_joining},
            overridable=False,
        )
    exit_boundary = staff.notice_period_end or staff.date_of_exit
    if exit_boundary and d_to > _to_date(exit_boundary):
        return RuleViolation(
            code="NOT_JOINED_OR_EXITED",
            severity="BLOCK",
            message=f"Booking extends beyond exit/notice-period end {exit_boundary}.",
            context={"exit_boundary": exit_boundary},
            overridable=False,
        )
    return None


def check_independence_conflict(db: Session, cand: AllocationCandidate, engagement: Engagement) -> RuleViolation | None:
    """R5: staff conflicted for this client, or any client in the same group."""
    from app.models.allocation import IndependenceDeclaration

    client = db.get(Client, engagement.client_id)
    if client is None:
        return None
    client_ids = {client.id}
    if client.group_id:
        group_clients = db.exec(select(Client).where(Client.group_id == client.group_id)).all()
        client_ids |= {c.id for c in group_clients}
    stmt = (
        select(IndependenceDeclaration)
        .where(IndependenceDeclaration.staff_id == cand.staff_id)
        .where(IndependenceDeclaration.client_id.in_(client_ids))  # type: ignore[attr-defined]
        .where(IndependenceDeclaration.is_conflicted == True)  # noqa: E712
        .where(IndependenceDeclaration.is_active == True)  # noqa: E712
    )
    conflict = db.exec(stmt).first()
    if conflict:
        return RuleViolation(
            code="INDEPENDENCE_CONFLICT",
            severity="BLOCK",
            message="Staff has a declared independence conflict on this client or its client group.",
            context={"declaration_id": str(conflict.id), "client_id": str(conflict.client_id)},
            overridable=False,
        )
    return None


def check_eqcr_independence(db: Session, cand: AllocationCandidate, engagement: Engagement) -> RuleViolation | None:
    """R6: the EQCR partner cannot also hold a non-EQCR role on the same engagement."""
    is_eqcr_candidate = cand.role_on_engagement == AllocationRole.EQCR or cand.staff_id == engagement.eqcr_partner_id
    if not is_eqcr_candidate:
        return None
    eqcr_staff_id = engagement.eqcr_partner_id if engagement.eqcr_partner_id else (
        cand.staff_id if cand.role_on_engagement == AllocationRole.EQCR else None
    )
    if eqcr_staff_id is None:
        return None
    stmt = (
        select(Allocation)
        .where(Allocation.engagement_id == cand.engagement_id)
        .where(Allocation.staff_id == eqcr_staff_id)
        .where(Allocation.is_active == True)  # noqa: E712
        .where(Allocation.role_on_engagement != AllocationRole.EQCR)
        .where(Allocation.status.in_([AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS, AllocationStatus.PROPOSED]))  # type: ignore[attr-defined]
        .where(Allocation.date_from <= cand.date_to)
        .where(Allocation.date_to >= cand.date_from)
    )
    if cand.exclude_allocation_id:
        stmt = stmt.where(Allocation.id != cand.exclude_allocation_id)
    other_role = db.exec(stmt).first()
    # Also catch the case where THIS candidate itself is a non-EQCR role for the current EQCR partner.
    if cand.role_on_engagement != AllocationRole.EQCR and cand.staff_id == engagement.eqcr_partner_id:
        return RuleViolation(
            code="EQCR_INDEPENDENCE",
            severity="BLOCK",
            message="This staff member is the engagement's EQCR partner and cannot also hold a delivery role.",
            context={"engagement_id": str(engagement.id)},
            overridable=False,
        )
    if other_role:
        return RuleViolation(
            code="EQCR_INDEPENDENCE",
            severity="BLOCK",
            message="EQCR partner already holds a non-EQCR role on this engagement for overlapping dates.",
            context={"conflicting_allocation_id": str(other_role.id)},
            overridable=False,
        )
    return None


def check_signing_partner_not_partner(cand: AllocationCandidate, staff: Staff) -> RuleViolation | None:
    """R7: signing/engagement partner must be a PARTNER with a valid ICAI membership no."""
    if cand.role_on_engagement not in (AllocationRole.SIGNING_PARTNER, AllocationRole.ENGAGEMENT_PARTNER):
        return None
    if staff.staff_category != StaffCategory.PARTNER or not staff.icai_membership_no:
        return RuleViolation(
            code="SIGNING_PARTNER_NOT_PARTNER",
            severity="BLOCK",
            message="Signing/engagement partner role requires staff_category=PARTNER with a valid ICAI membership no.",
            context={"staff_category": staff.staff_category, "icai_membership_no": staff.icai_membership_no},
            overridable=False,
        )
    return None


def check_ep_rotation(cand: AllocationCandidate, staff: Staff, engagement: Engagement, client: Client | None) -> RuleViolation | None:
    """R8: EP rotation due — WARN normally, BLOCK if the client is a PIE."""
    if cand.role_on_engagement != AllocationRole.ENGAGEMENT_PARTNER or not engagement.ep_rotation_due_fy:
        return None
    if engagement.financial_year < engagement.ep_rotation_due_fy:
        return None
    is_pie = bool(client and client.is_pie)
    return RuleViolation(
        code="EP_ROTATION_DUE",
        severity="BLOCK" if is_pie else "WARN",
        message=f"Engagement partner rotation was due by {engagement.ep_rotation_due_fy} (s.139(2)).",
        context={"ep_rotation_due_fy": engagement.ep_rotation_due_fy, "is_pie": is_pie},
        overridable=not is_pie,
        override_role="PARTNER",
    )


def check_no_qualified_supervisor(db: Session, cand: AllocationCandidate, engagement: Engagement) -> RuleViolation | None:
    """R9: an engagement with >=1 article needs a supervisor of grade <= Assistant Manager overlapping."""
    candidate_staff = db.get(Staff, cand.staff_id)
    is_article_candidate = candidate_staff is not None and candidate_staff.staff_category in (
        StaffCategory.ARTICLED_ASSISTANT,
        StaffCategory.INDUSTRIAL_TRAINEE,
    )
    if not is_article_candidate:
        return None

    stmt = (
        select(Allocation, Staff)
        .join(Staff, Allocation.staff_id == Staff.id)
        .where(Allocation.engagement_id == cand.engagement_id)
        .where(Allocation.is_active == True)  # noqa: E712
        .where(Allocation.status.in_([AllocationStatus.CONFIRMED, AllocationStatus.IN_PROGRESS, AllocationStatus.PROPOSED]))  # type: ignore[attr-defined]
        .where(Allocation.date_from <= cand.date_to)
        .where(Allocation.date_to >= cand.date_from)
        .where(Staff.grade_rank <= QUALIFIED_SUPERVISOR_MAX_GRADE_RANK)
    )
    if cand.exclude_allocation_id:
        stmt = stmt.where(Allocation.id != cand.exclude_allocation_id)
    supervisor = db.exec(stmt).first()
    if supervisor is None:
        return RuleViolation(
            code="NO_QUALIFIED_SUPERVISOR",
            severity="BLOCK",
            message="No member of grade >= Assistant Manager is booked on this engagement for the article's dates.",
            context={"engagement_id": str(engagement.id)},
            overridable=False,
        )
    return None


def check_eqcr_missing(cand: AllocationCandidate, engagement: Engagement) -> RuleViolation | None:
    """R10: engagement is flagged eqcr_required but has no EQCR partner assigned yet."""
    if not engagement.eqcr_required or engagement.eqcr_partner_id:
        return None
    if cand.role_on_engagement == AllocationRole.EQCR:
        return None  # this booking would satisfy it
    return RuleViolation(
        code="EQCR_MISSING",
        severity="WARN",
        message="Engagement requires an EQCR partner but none is assigned yet.",
        context={"engagement_id": str(engagement.id)},
        overridable=True,
        override_role="PARTNER",
    )


def check_skill_gap(db: Session, cand: AllocationCandidate, engagement: Engagement) -> RuleViolation | None:
    """R11: engagement lists required specialist skills the staff doesn't hold."""
    required_codes = engagement.requires_specialist_skills or []
    if not required_codes:
        return None
    skills = db.exec(select(Skill).where(Skill.code.in_(required_codes))).all()  # type: ignore[attr-defined]
    if not skills:
        return None
    skill_id_by_code = {s.code: s.id for s in skills}
    held_ids = set(
        db.exec(
            select(StaffSkill.skill_id)
            .where(StaffSkill.staff_id == cand.staff_id)
            .where(StaffSkill.is_active == True)  # noqa: E712
        ).all()
    )
    missing = [code for code, sid in skill_id_by_code.items() if sid not in held_ids]
    if missing:
        return RuleViolation(
            code="SKILL_GAP",
            severity="WARN",
            message=f"Staff is missing required specialist skill(s): {', '.join(missing)}.",
            context={"missing_skills": missing},
            overridable=True,
            override_role="RESOURCE_MANAGER",
        )
    return None


def check_grade_mix_breach(db: Session, cand: AllocationCandidate, staff: Staff) -> RuleViolation | None:
    """R12: article:qualified-staff ratio on the engagement exceeds the configured cap."""
    if staff.staff_category not in ARTICLE_CATEGORIES:
        return None
    max_ratio = get_config_value(db, "max_article_ratio", get_settings().default_max_article_ratio)
    rows = _overlapping_engagement_allocations(db, cand.engagement_id, cand.date_from, cand.date_to, cand.exclude_allocation_id)
    staff_ids = {r.staff_id for r in rows} | {cand.staff_id}
    staff_map = {s.id: s for s in db.exec(select(Staff).where(Staff.id.in_(staff_ids))).all()}  # type: ignore[attr-defined]
    article_count, qualified_count = 1, 0  # 1 = the candidate itself
    for r in rows:
        s = staff_map.get(r.staff_id)
        if s is None:
            continue
        if s.staff_category in ARTICLE_CATEGORIES:
            article_count += 1
        else:
            qualified_count += 1
    if qualified_count == 0 or (article_count / qualified_count) > max_ratio:
        return RuleViolation(
            code="GRADE_MIX_BREACH",
            severity="WARN",
            message=f"Article:qualified ratio on this engagement would be {article_count}:{qualified_count}, exceeding the {max_ratio}:1 cap.",
            context={"article_count": article_count, "qualified_count": qualified_count, "max_ratio": max_ratio},
            overridable=True,
            override_role="RESOURCE_MANAGER",
        )
    return None


def check_icai_training_limit(db: Session, cand: AllocationCandidate, staff: Staff) -> RuleViolation | None:
    """R13: ICAI Reg. 43 secondment limits (§4 default: cap 2 articles/principal, 12
    months aggregate). The schema has no dedicated secondment record, so this is
    approximated against `SECONDMENT`-type `non_availability` rows — see docs/decisions.md."""
    if staff.staff_category not in ARTICLE_CATEGORIES:
        return None
    months_cap = get_config_value(db, "article_secondment_months_cap", get_settings().default_article_secondment_months_cap)
    principal_cap = get_config_value(db, "article_secondment_cap", get_settings().default_article_secondment_cap)

    own_secondments = db.exec(
        select(NonAvailability)
        .where(NonAvailability.staff_id == cand.staff_id)
        .where(NonAvailability.type == NonAvailabilityType.SECONDMENT.value)
        .where(NonAvailability.status == NonAvailabilityStatus.APPROVED.value)
        .where(NonAvailability.is_active == True)  # noqa: E712
    ).all()
    total_days = sum((_to_date(n.date_to) - _to_date(n.date_from)).days + 1 for n in own_secondments)
    if total_days / 30.0 > months_cap:
        return RuleViolation(
            code="ICAI_TRAINING_LIMIT",
            severity="WARN",
            message=f"Staff's aggregate secondment ({total_days} days) exceeds the {months_cap}-month ICAI cap.",
            context={"total_secondment_days": total_days, "months_cap": months_cap},
            overridable=True,
            override_role="HR",
        )

    if staff.articleship_principal_id and own_secondments:
        siblings = db.exec(
            select(Staff)
            .where(Staff.articleship_principal_id == staff.articleship_principal_id)
            .where(Staff.staff_category.in_(ARTICLE_CATEGORIES))  # type: ignore[attr-defined]
            .where(Staff.is_active == True)  # noqa: E712
            .where(Staff.secondment_flag == True)  # noqa: E712
        ).all()
        if len(siblings) > principal_cap:
            return RuleViolation(
                code="ICAI_TRAINING_LIMIT",
                severity="WARN",
                message=f"Principal already has {len(siblings)} article(s) on secondment, exceeding the cap of {principal_cap}.",
                context={"principal_id": str(staff.articleship_principal_id), "count": len(siblings), "cap": principal_cap},
                overridable=True,
                override_role="HR",
            )
    return None


def check_article_hours_breach(db: Session, cand: AllocationCandidate, staff: Staff) -> RuleViolation | None:
    """R14: article weekly working-hours norm (35 hrs/week, §5) would be breached."""
    if staff.staff_category not in ARTICLE_CATEGORIES:
        return None
    norm = ARTICLE_WEEKLY_HOURS_NORM
    d_from, d_to = _to_date(cand.date_from), _to_date(cand.date_to)
    week_hours: dict[tuple[int, int], float] = {}
    for day in _daterange(d_from, d_to):
        if day.weekday() < 5:
            week_hours[_week_key(day)] = week_hours.get(_week_key(day), 0.0) + (cand.allocation_pct / 100) * (norm / 5)
    if not week_hours:
        return None
    widen_from, widen_to = d_from - timedelta(days=7), d_to + timedelta(days=7)
    existing = _overlapping_active_allocations(db, cand.staff_id, widen_from.isoformat(), widen_to.isoformat(), cand.exclude_allocation_id)
    for alloc in existing:
        a_from, a_to = _to_date(alloc.date_from), _to_date(alloc.date_to)
        for day in _daterange(a_from, a_to):
            key = _week_key(day)
            if key in week_hours and day.weekday() < 5:
                week_hours[key] = week_hours.get(key, 0.0) + (alloc.allocation_pct / 100) * (norm / 5)
    worst_week, worst_hours = None, 0.0
    for key, hrs in week_hours.items():
        if hrs > worst_hours:
            worst_week, worst_hours = key, hrs
    if worst_hours > norm:
        return RuleViolation(
            code="ARTICLE_HOURS_BREACH",
            severity="WARN",
            message=f"Projected {worst_hours:.1f} hrs in ISO week {worst_week[1]}/{worst_week[0]}, exceeding the {norm:.0f}-hr article norm.",
            context={"iso_week": list(worst_week), "hours": worst_hours, "norm": norm},
            overridable=True,
            override_role="RESOURCE_MANAGER",
        )
    return None


def check_sustained_overload(db: Session, cand: AllocationCandidate) -> RuleViolation | None:
    """R15: this booking would leave staff >= 90% allocated for `burnout_weeks`
    consecutive weeks (the burnout watchlist threshold, §8)."""
    burnout_weeks = int(get_config_value(db, "burnout_weeks", get_settings().default_burnout_weeks))
    d_from, d_to = _to_date(cand.date_from), _to_date(cand.date_to)
    window_from = d_from - timedelta(days=7 * burnout_weeks)
    window_to = d_to + timedelta(days=7 * burnout_weeks)
    existing = _overlapping_active_allocations(db, cand.staff_id, window_from.isoformat(), window_to.isoformat(), cand.exclude_allocation_id)
    week_pcts: dict[tuple[int, int], list[float]] = {}
    for day in _daterange(window_from, window_to):
        if day.weekday() >= 5:
            continue
        total = cand.allocation_pct if d_from <= day <= d_to else 0.0
        for alloc in existing:
            if _to_date(alloc.date_from) <= day <= _to_date(alloc.date_to):
                total += alloc.allocation_pct
        week_pcts.setdefault(_week_key(day), []).append(total)
    run = best_run = 0
    for key in sorted(week_pcts.keys()):
        avg = sum(week_pcts[key]) / len(week_pcts[key])
        run = run + 1 if avg >= SUSTAINED_OVERLOAD_THRESHOLD_PCT else 0
        best_run = max(best_run, run)
    if best_run >= burnout_weeks:
        return RuleViolation(
            code="SUSTAINED_OVERLOAD",
            severity="WARN",
            message=f"This booking would leave staff >= {SUSTAINED_OVERLOAD_THRESHOLD_PCT:.0f}% allocated for {best_run} consecutive weeks (threshold {burnout_weeks}).",
            context={"consecutive_weeks": best_run, "threshold_weeks": burnout_weeks},
            overridable=True,
            override_role="RESOURCE_MANAGER",
        )
    return None


def check_outstation_breach(db: Session, cand: AllocationCandidate, staff: Staff) -> RuleViolation | None:
    """R16: monthly outstation-day cap (`staff.max_outstation_days_per_month`) breached."""
    if staff.max_outstation_days_per_month is None or cand.office_id is None or staff.base_office_id is None:
        return None
    if cand.office_id == staff.base_office_id:
        return None
    d_from, d_to = _to_date(cand.date_from), _to_date(cand.date_to)
    for year, month in sorted({(day.year, day.month) for day in _daterange(d_from, d_to)}):
        month_from = date(year, month, 1)
        month_to = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
        cand_days = _business_days(max(d_from, month_from), min(d_to, month_to))
        existing = _overlapping_active_allocations(db, cand.staff_id, month_from.isoformat(), month_to.isoformat(), cand.exclude_allocation_id)
        existing_days = 0
        for alloc in existing:
            if alloc.office_id and alloc.office_id != staff.base_office_id:
                a_from, a_to = max(_to_date(alloc.date_from), month_from), min(_to_date(alloc.date_to), month_to)
                if a_from <= a_to:
                    existing_days += (a_to - a_from).days + 1
        total_days = cand_days + existing_days
        if total_days > staff.max_outstation_days_per_month:
            return RuleViolation(
                code="OUTSTATION_BREACH",
                severity="WARN",
                message=f"Outstation days in {year}-{month:02d} would total {total_days}, exceeding the cap of {staff.max_outstation_days_per_month}.",
                context={"year": year, "month": month, "total_days": total_days, "cap": staff.max_outstation_days_per_month},
                overridable=True,
                override_role="RESOURCE_MANAGER",
            )
    return None


def check_location_mismatch(cand: AllocationCandidate, staff: Staff) -> RuleViolation | None:
    """R17: booking office differs from the staff member's base office (informational)."""
    if cand.office_id is None or staff.base_office_id is None or cand.office_id == staff.base_office_id:
        return None
    return RuleViolation(
        code="LOCATION_MISMATCH",
        severity="INFO",
        message="Booking office differs from staff's base office.",
        context={"office_id": str(cand.office_id), "base_office_id": str(staff.base_office_id)},
        overridable=True,
    )


def check_budget_overrun(db: Session, cand: AllocationCandidate, engagement: Engagement, staff: Staff) -> RuleViolation | None:
    """R18: projected staff-cost / fee ratio on the engagement exceeds the configured
    ceiling (`max_cost_ratio`). Hours are estimated at 8 hrs/business-day per
    allocation_pct — the same approximation used elsewhere pre-capacity_daily."""
    if not engagement.fee_amount or engagement.fee_amount <= 0:
        return None
    max_ratio = get_config_value(db, "max_cost_ratio", get_settings().default_max_cost_ratio)
    rows = _overlapping_engagement_allocations(db, cand.engagement_id, cand.date_from, cand.date_to, cand.exclude_allocation_id)
    staff_ids = {r.staff_id for r in rows} | {cand.staff_id}
    staff_map = {s.id: s for s in db.exec(select(Staff).where(Staff.id.in_(staff_ids))).all()}  # type: ignore[attr-defined]

    def _cost(rate: float | None, pct: float, d_from: str, d_to: str) -> float:
        return (rate or 0.0) * (pct / 100) * _business_days(_to_date(d_from), _to_date(d_to)) * 8

    total_cost = _cost(staff.cost_rate_per_hour, cand.allocation_pct, cand.date_from, cand.date_to)
    for r in rows:
        s = staff_map.get(r.staff_id)
        total_cost += _cost(s.cost_rate_per_hour if s else None, r.allocation_pct, r.date_from, r.date_to)
    if total_cost <= 0:
        return None
    ratio = total_cost / engagement.fee_amount
    if ratio > max_ratio:
        return RuleViolation(
            code="BUDGET_OVERRUN",
            severity="WARN",
            message=f"Projected staff cost / fee ratio is {ratio:.0%}, exceeding the {max_ratio:.0%} ceiling.",
            context={"ratio": ratio, "max_ratio": max_ratio, "projected_cost": total_cost, "fee_amount": engagement.fee_amount},
            overridable=True,
            override_role="PARTNER",
        )
    return None


def check_deadline_risk(cand: AllocationCandidate, engagement: Engagement) -> RuleViolation | None:
    """R19: booking extends past the engagement's reporting/statutory deadline."""
    deadline = engagement.reporting_deadline or engagement.statutory_due_date
    if not deadline:
        return None
    if _to_date(cand.date_to) > _to_date(deadline):
        return RuleViolation(
            code="DEADLINE_RISK",
            severity="WARN",
            message=f"Booking extends to {cand.date_to}, past the engagement's reporting deadline {deadline}.",
            context={"deadline": deadline, "date_to": cand.date_to},
            overridable=True,
            override_role="PARTNER",
        )
    return None


def check_no_exposure_diversity(db: Session, cand: AllocationCandidate, staff: Staff, engagement: Engagement) -> RuleViolation | None:
    """R20: article accumulating more than `max_days_single_client` days on one
    client — a training-diversity guideline, informational only."""
    if staff.staff_category not in ARTICLE_CATEGORIES:
        return None
    max_days = get_config_value(db, "max_days_single_client", get_settings().default_max_days_single_client)
    stmt = (
        select(Allocation, Engagement.client_id)
        .join(Engagement, Allocation.engagement_id == Engagement.id)  # type: ignore[arg-type]
        .where(Allocation.staff_id == cand.staff_id)
        .where(Allocation.is_active == True)  # noqa: E712
        .where(Engagement.client_id == engagement.client_id)
    )
    if cand.exclude_allocation_id:
        stmt = stmt.where(Allocation.id != cand.exclude_allocation_id)
    rows = db.exec(stmt).all()
    existing_days = sum((_to_date(a.date_to) - _to_date(a.date_from)).days + 1 for a, _ in rows)
    cand_days = (_to_date(cand.date_to) - _to_date(cand.date_from)).days + 1
    total_days = existing_days + cand_days
    if total_days > max_days:
        return RuleViolation(
            code="NO_EXPOSURE_DIVERSITY",
            severity="INFO",
            message=f"Staff would have {total_days} days on this client, above the {max_days}-day training-diversity guideline.",
            context={"total_days": total_days, "max_days": max_days, "client_id": str(engagement.client_id)},
            overridable=True,
        )
    return None


def check_exiting_staff(cand: AllocationCandidate, staff: Staff) -> RuleViolation | None:
    """R21: booking falls within the final 20% of staff's notice period."""
    if not staff.notice_period_end:
        return None
    notice_end = _to_date(staff.notice_period_end)
    notice_start = notice_end - timedelta(days=STANDARD_NOTICE_PERIOD_DAYS)
    threshold = notice_start + timedelta(days=int(STANDARD_NOTICE_PERIOD_DAYS * 0.8))
    d_from = _to_date(cand.date_from)
    if threshold <= d_from <= notice_end:
        return RuleViolation(
            code="EXITING_STAFF",
            severity="WARN",
            message=f"Booking starts {d_from.isoformat()}, within the final 20% of staff's notice period (ends {notice_end.isoformat()}).",
            context={"notice_period_end": staff.notice_period_end, "date_from": cand.date_from},
            overridable=True,
            override_role="RESOURCE_MANAGER",
        )
    return None


def check_unapproved_pipeline(engagement: Engagement, client: Client | None) -> RuleViolation | None:
    """R22: engagement is still PIPELINE or client acceptance isn't finalised —
    booking is provisional (informational only)."""
    is_pipeline = engagement.status == EngagementStatus.PIPELINE
    is_unaccepted = bool(client) and client.acceptance_status != AcceptanceStatus.ACCEPTED
    if not (is_pipeline or is_unaccepted):
        return None
    reason = "engagement status is PIPELINE" if is_pipeline else "client acceptance is still pending"
    return RuleViolation(
        code="UNAPPROVED_PIPELINE",
        severity="INFO",
        message=f"Booking is provisional — {reason}.",
        context={"engagement_status": engagement.status, "acceptance_status": client.acceptance_status if client else None},
        overridable=True,
    )


def check_duplicate_role(db: Session, cand: AllocationCandidate) -> RuleViolation | None:
    """R23: singleton engagement roles (signing partner, EP, EQCR, engagement manager)
    can only be held by one staff member at a time."""
    if cand.role_on_engagement not in SINGLETON_ROLES:
        return None
    rows = _overlapping_engagement_allocations(db, cand.engagement_id, cand.date_from, cand.date_to, cand.exclude_allocation_id)
    holder = next((r for r in rows if r.role_on_engagement == cand.role_on_engagement and r.staff_id != cand.staff_id), None)
    if holder:
        return RuleViolation(
            code="DUPLICATE_ROLE",
            severity="BLOCK",
            message=f"{cand.role_on_engagement} is already held by another staff member for overlapping dates.",
            context={"conflicting_allocation_id": str(holder.id), "role": cand.role_on_engagement},
            overridable=False,
        )
    return None


def check_cooling_off(db: Session, cand: AllocationCandidate, engagement: Engagement) -> RuleViolation | None:
    """R24: staff declared prior employment with this client (or its group). The schema
    stores this as a boolean (`held_employment_last_2yrs`), not a date the employment
    ended, so this is presence-based rather than computing days left in the cooling-off
    window — see docs/decisions.md."""
    from app.models.allocation import IndependenceDeclaration

    client = db.get(Client, engagement.client_id)
    if client is None:
        return None
    client_ids = {client.id}
    if client.group_id:
        client_ids |= {c.id for c in db.exec(select(Client).where(Client.group_id == client.group_id)).all()}
    cooling_off_months = get_config_value(db, "cooling_off_months", get_settings().default_cooling_off_months)
    stmt = (
        select(IndependenceDeclaration)
        .where(IndependenceDeclaration.staff_id == cand.staff_id)
        .where(IndependenceDeclaration.client_id.in_(client_ids))  # type: ignore[attr-defined]
        .where(IndependenceDeclaration.held_employment_last_2yrs == True)  # noqa: E712
        .where(IndependenceDeclaration.is_active == True)  # noqa: E712
    )
    decl = db.exec(stmt).first()
    if decl:
        return RuleViolation(
            code="COOLING_OFF",
            severity="WARN",
            message=f"Staff declared prior employment with this client (or its group) within the last 2 years — confirm the {cooling_off_months}-month cooling-off period has elapsed.",
            context={"declaration_id": str(decl.id), "cooling_off_months": cooling_off_months},
            overridable=True,
            override_role="PARTNER",
        )
    return None


def validate_allocation(db: Session, cand: AllocationCandidate) -> list[RuleViolation]:
    violations: list[RuleViolation] = []

    engagement = db.get(Engagement, cand.engagement_id)
    staff = db.get(Staff, cand.staff_id)
    if engagement is None or staff is None:
        violations.append(
            RuleViolation(
                code="NOT_FOUND",
                severity="BLOCK",
                message="Engagement or staff not found.",
                overridable=False,
            )
        )
        return violations

    client = db.get(Client, engagement.client_id)

    for check in (
        lambda: check_overallocation(db, cand),
        lambda: check_leave_conflict(db, cand),
        lambda: check_exam_leave(db, cand, staff),
        lambda: check_not_joined_or_exited(cand, staff),
        lambda: check_independence_conflict(db, cand, engagement),
        lambda: check_eqcr_independence(db, cand, engagement),
        lambda: check_signing_partner_not_partner(cand, staff),
        lambda: check_ep_rotation(cand, staff, engagement, client),
        lambda: check_no_qualified_supervisor(db, cand, engagement),
        lambda: check_eqcr_missing(cand, engagement),
        lambda: check_skill_gap(db, cand, engagement),
        lambda: check_grade_mix_breach(db, cand, staff),
        lambda: check_icai_training_limit(db, cand, staff),
        lambda: check_article_hours_breach(db, cand, staff),
        lambda: check_sustained_overload(db, cand),
        lambda: check_outstation_breach(db, cand, staff),
        lambda: check_location_mismatch(cand, staff),
        lambda: check_budget_overrun(db, cand, engagement, staff),
        lambda: check_deadline_risk(cand, engagement),
        lambda: check_no_exposure_diversity(db, cand, staff, engagement),
        lambda: check_exiting_staff(cand, staff),
        lambda: check_unapproved_pipeline(engagement, client),
        lambda: check_duplicate_role(db, cand),
        lambda: check_cooling_off(db, cand, engagement),
    ):
        result = check()
        if result:
            violations.append(result)

    return violations


def has_blocking(violations: list[RuleViolation]) -> bool:
    return any(v.severity == "BLOCK" for v in violations)
