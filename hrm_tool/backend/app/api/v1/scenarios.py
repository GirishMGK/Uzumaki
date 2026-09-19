"""What-if scenario planning (§8, Phase P10).

A scenario is a sandbox: its lines reference real staff/engagements so the
real conflict engine can evaluate them accurately, but nothing is booked
until `/promote` writes real `Allocation` rows — through the exact same
validate-then-write path `POST /allocations` uses, one line at a time.
Promote only ever writes lines with zero violations (BLOCK or WARN); a
WARN needs a human-typed override reason per rule (§4), which a bulk
promote has no way to supply, so those lines are skipped and reported
rather than silently pushed through.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.audit import write_audit_log
from app.core.deps import get_client_ip, get_current_user, get_db, require_roles
from app.core.soft_delete import reject_hard_delete
from app.models.allocation import Allocation
from app.models.enums import AllocationStatus, AuditAction, UserRole
from app.models.scenario import Scenario, ScenarioAllocation
from app.models.user import User
from app.schemas.scenario import (
    ScenarioCreate,
    ScenarioImpactOut,
    ScenarioLineCreate,
    ScenarioLineRead,
    ScenarioPromoteResult,
    ScenarioRead,
)
from app.services.capacity_materializer import recompute_range
from app.services.scenario_service import evaluate_scenario

router = APIRouter()

WRITE_ROLES = (UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER, UserRole.MANAGER)


@router.get("", response_model=list[ScenarioRead])
def list_scenarios(
    status_: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> list[ScenarioRead]:
    stmt = select(Scenario).where(Scenario.is_active == True)  # noqa: E712
    if status_:
        stmt = stmt.where(Scenario.status == status_)
    return list(db.exec(stmt).all())


@router.get("/{scenario_id}", response_model=ScenarioRead)
def get_scenario(scenario_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Scenario:
    row = db.get(Scenario, scenario_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return row


@router.post("", response_model=ScenarioRead, status_code=status.HTTP_201_CREATED)
def create_scenario(
    payload: ScenarioCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles(*WRITE_ROLES)),
) -> Scenario:
    row = Scenario(**payload.model_dump(), created_by=user.id, updated_by=user.id)
    db.add(row)
    db.flush()
    write_audit_log(
        db, entity_type="scenarios", entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
        before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{scenario_id}")
def discard_scenario(
    scenario_id: uuid.UUID, request: Request, hard: bool = False,
    db: Session = Depends(get_db), user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    reject_hard_delete(hard)
    row = db.get(Scenario, scenario_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    before = row.model_copy()
    row.status = "DISCARDED"
    row.is_active = False
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="scenarios", entity_id=row.id, action=AuditAction.DELETE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"status": "discarded", "id": str(scenario_id)}


@router.get("/{scenario_id}/lines", response_model=list[ScenarioLineRead])
def list_lines(scenario_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ScenarioAllocation]:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None or not scenario.is_active:
        raise HTTPException(status_code=404, detail="Scenario not found")
    stmt = select(ScenarioAllocation).where(ScenarioAllocation.scenario_id == scenario_id).where(ScenarioAllocation.is_active == True)  # noqa: E712
    return list(db.exec(stmt).all())


@router.post("/{scenario_id}/lines", response_model=ScenarioLineRead, status_code=status.HTTP_201_CREATED)
def add_line(
    scenario_id: uuid.UUID, payload: ScenarioLineCreate, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_roles(*WRITE_ROLES)),
) -> ScenarioAllocation:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None or not scenario.is_active:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if scenario.status != "DRAFT":
        raise HTTPException(status_code=422, detail=f"Cannot add lines to a {scenario.status} scenario")
    row = ScenarioAllocation(scenario_id=scenario_id, **payload.model_dump(), created_by=user.id, updated_by=user.id)
    db.add(row)
    db.flush()
    write_audit_log(
        db, entity_type="scenario_allocations", entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
        before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{scenario_id}/lines/{line_id}")
def remove_line(
    scenario_id: uuid.UUID, line_id: uuid.UUID, request: Request, hard: bool = False,
    db: Session = Depends(get_db), user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    reject_hard_delete(hard)
    row = db.get(ScenarioAllocation, line_id)
    if row is None or row.scenario_id != scenario_id:
        raise HTTPException(status_code=404, detail="Scenario line not found")
    before = row.model_copy()
    row.is_active = False
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.id
    db.add(row)
    write_audit_log(
        db, entity_type="scenario_allocations", entity_id=row.id, action=AuditAction.DELETE, actor_id=user.id,
        before=before, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"status": "removed", "id": str(line_id)}


@router.get("/{scenario_id}/impact", response_model=ScenarioImpactOut)
def get_impact(scenario_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ScenarioImpactOut:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None or not scenario.is_active:
        raise HTTPException(status_code=404, detail="Scenario not found")
    impact = evaluate_scenario(db, scenario)
    return ScenarioImpactOut(
        scenario_id=impact.scenario_id, has_blocking=impact.has_blocking,
        lines=[
            {
                "line_id": ln.line_id, "staff_id": ln.staff_id, "staff_name": ln.staff_name,
                "engagement_code": ln.engagement_code, "client_name": ln.client_name,
                "date_from": ln.date_from, "date_to": ln.date_to, "allocation_pct": ln.allocation_pct,
                "violations": [v.__dict__ for v in ln.violations],
            }
            for ln in impact.lines
        ],
        staff_impact=[si.__dict__ for si in impact.staff_impact],
    )


@router.post("/{scenario_id}/promote", response_model=ScenarioPromoteResult)
def promote_scenario(
    scenario_id: uuid.UUID, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles(*WRITE_ROLES)),
) -> ScenarioPromoteResult:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None or not scenario.is_active:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if scenario.status != "DRAFT":
        raise HTTPException(status_code=422, detail=f"Scenario is already {scenario.status}")

    impact = evaluate_scenario(db, scenario)
    promoted_count = 0
    skipped: list[dict] = []
    affected_staff_ids: set[uuid.UUID] = set()

    lines_by_id = {ln.line_id: ln for ln in impact.lines}
    for line_id, line_result in lines_by_id.items():
        line = db.get(ScenarioAllocation, line_id)
        if line is None:
            continue
        blocking_or_warn = [v for v in line_result.violations if v.severity in ("BLOCK", "WARN")]
        if blocking_or_warn:
            skipped.append(
                {
                    "line_id": str(line_id), "staff_name": line_result.staff_name,
                    "engagement_code": line_result.engagement_code,
                    "reasons": [f"{v.code}: {v.message}" for v in blocking_or_warn],
                }
            )
            continue
        row = Allocation(
            engagement_id=line.engagement_id, staff_id=line.staff_id, role_on_engagement=line.role_on_engagement,
            date_from=line.date_from, date_to=line.date_to, allocation_pct=line.allocation_pct,
            status=AllocationStatus.DRAFT, notes=f"Promoted from scenario '{scenario.name}'" + (f" — {line.notes}" if line.notes else ""),
            requested_by=user.id, created_by=user.id, updated_by=user.id,
        )
        db.add(row)
        db.flush()
        write_audit_log(
            db, entity_type="allocations", entity_id=row.id, action=AuditAction.CREATE, actor_id=user.id,
            before=None, after=row, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
        )
        line.promoted_allocation_id = row.id
        db.add(line)
        promoted_count += 1
        affected_staff_ids.add(line.staff_id)

    scenario.status = "PROMOTED"
    scenario.updated_by = user.id
    scenario.updated_at = datetime.now(timezone.utc)
    db.add(scenario)
    write_audit_log(
        db, entity_type="scenarios", entity_id=scenario.id, action=AuditAction.APPROVE, actor_id=user.id,
        before=None, after=scenario, ip=get_client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    if affected_staff_ids:
        d_from = datetime.strptime(scenario.date_from, "%Y-%m-%d").date()
        d_to = datetime.strptime(scenario.date_to, "%Y-%m-%d").date()
        recompute_range(db, d_from, d_to, staff_ids=list(affected_staff_ids))

    return ScenarioPromoteResult(scenario_id=scenario_id, promoted_count=promoted_count, skipped=skipped)
