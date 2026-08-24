"""Scenario impact checking (§8, Phase P10).

A scenario line is checked two ways:

1. Against real committed data — the exact same `validate_allocation`
   (R1-R24) the live scheduler and `/allocations` run, since a scenario
   line references real `staff_id`/`engagement_id` rows.
2. Against its scenario siblings — `validate_allocation` only knows about
   committed `allocations` rows, so a day-by-day overallocation check
   across the *other* lines in the same scenario is done here, the same
   arithmetic as R1's `check_overallocation`.

Nothing here writes to `allocations`; that only happens on `/promote`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.models.client import Client
from app.models.engagement import Engagement
from app.models.scenario import Scenario, ScenarioAllocation
from app.models.staff import Staff
from app.services.capacity_report import get_staff_utilisation
from app.services.conflict_engine import AllocationCandidate, RuleViolation, validate_allocation


def _to_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _overlap_days(a_from: str, a_to: str, b_from: str, b_to: str) -> int:
    start = max(_to_date(a_from), _to_date(b_from))
    end = min(_to_date(a_to), _to_date(b_to))
    return max(0, (end - start).days + 1)


def _scenario_siblings_conflict(line: ScenarioAllocation, siblings: list[ScenarioAllocation]) -> RuleViolation | None:
    """R1-style day-by-day overallocation check, but against sibling scenario
    lines rather than committed allocations (validate_allocation can't see those)."""
    others = [s for s in siblings if s.staff_id == line.staff_id and s.id != line.id]
    if not others:
        return None
    d_from, d_to = _to_date(line.date_from), _to_date(line.date_to)
    worst_day, worst_pct = None, 0.0
    day = d_from
    while day <= d_to:
        total = line.allocation_pct
        for o in others:
            if _to_date(o.date_from) <= day <= _to_date(o.date_to):
                total += o.allocation_pct
        if total > worst_pct:
            worst_pct, worst_day = total, day
        day += timedelta(days=1)
    if worst_pct > 100:
        return RuleViolation(
            code="SCENARIO_OVERALLOCATION",
            severity="BLOCK",
            message=f"Within this scenario, staff would be allocated {worst_pct:.0f}% on {worst_day.isoformat()} across sibling lines.",
            context={"date": worst_day.isoformat(), "total_pct": worst_pct},
            overridable=False,
        )
    return None


@dataclass
class ScenarioLineResult:
    line_id: uuid.UUID
    staff_id: uuid.UUID
    staff_name: str
    engagement_code: str
    client_name: str
    date_from: str
    date_to: str
    allocation_pct: float
    violations: list[RuleViolation]


@dataclass
class StaffImpact:
    staff_id: uuid.UUID
    staff_name: str
    current_util_pct: float
    added_pct: float
    projected_util_pct: float


@dataclass
class ScenarioImpact:
    scenario_id: uuid.UUID
    has_blocking: bool
    lines: list[ScenarioLineResult]
    staff_impact: list[StaffImpact]


def evaluate_scenario(db: Session, scenario: Scenario) -> ScenarioImpact:
    lines = list(
        db.exec(
            select(ScenarioAllocation)
            .where(ScenarioAllocation.scenario_id == scenario.id)
            .where(ScenarioAllocation.is_active == True)  # noqa: E712
        ).all()
    )
    staff_by_id = {s.id: s for s in db.exec(select(Staff)).all()}
    engagement_by_id = {e.id: e for e in db.exec(select(Engagement)).all()}
    client_by_id = {c.id: c for c in db.exec(select(Client)).all()}

    line_results: list[ScenarioLineResult] = []
    has_blocking = False
    for line in lines:
        cand = AllocationCandidate(
            engagement_id=line.engagement_id, staff_id=line.staff_id, role_on_engagement=line.role_on_engagement,
            date_from=line.date_from, date_to=line.date_to, allocation_pct=line.allocation_pct,
        )
        violations = validate_allocation(db, cand)
        sibling_violation = _scenario_siblings_conflict(line, lines)
        if sibling_violation:
            violations.append(sibling_violation)
        if any(v.severity == "BLOCK" for v in violations):
            has_blocking = True
        staff = staff_by_id.get(line.staff_id)
        engagement = engagement_by_id.get(line.engagement_id)
        client = client_by_id.get(engagement.client_id) if engagement else None
        line_results.append(
            ScenarioLineResult(
                line_id=line.id, staff_id=line.staff_id, staff_name=staff.full_name if staff else "",
                engagement_code=engagement.engagement_code if engagement else "",
                client_name=client.name if client else "", date_from=line.date_from, date_to=line.date_to,
                allocation_pct=line.allocation_pct, violations=violations,
            )
        )

    window_days = max(1, (_to_date(scenario.date_to) - _to_date(scenario.date_from)).days + 1)
    staff_ids = list({line.staff_id for line in lines})
    current_by_staff = {
        row["staff_id"]: row for row in get_staff_utilisation(db, _to_date(scenario.date_from), _to_date(scenario.date_to), staff_ids=staff_ids)
    } if staff_ids else {}

    added_pct_by_staff: dict[uuid.UUID, float] = {}
    for line in lines:
        overlap = _overlap_days(line.date_from, line.date_to, scenario.date_from, scenario.date_to)
        added_pct_by_staff[line.staff_id] = added_pct_by_staff.get(line.staff_id, 0.0) + line.allocation_pct * overlap / window_days

    staff_impact = []
    for sid in staff_ids:
        staff = staff_by_id.get(sid)
        current = current_by_staff.get(sid)
        current_util = current["utilisation_pct"] if current else 0.0
        added = round(added_pct_by_staff.get(sid, 0.0), 1)
        staff_impact.append(
            StaffImpact(
                staff_id=sid, staff_name=staff.full_name if staff else "",
                current_util_pct=current_util, added_pct=added,
                projected_util_pct=round(min(current_util + added, 200.0), 1),
            )
        )

    return ScenarioImpact(scenario_id=scenario.id, has_blocking=has_blocking, lines=line_results, staff_impact=staff_impact)
