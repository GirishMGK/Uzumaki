import uuid

from pydantic import BaseModel

from app.models.enums import AllocationRole


class ScenarioCreate(BaseModel):
    name: str
    description: str | None = None
    date_from: str
    date_to: str


class ScenarioRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    date_from: str
    date_to: str
    status: str
    is_active: bool


class ScenarioLineCreate(BaseModel):
    staff_id: uuid.UUID
    engagement_id: uuid.UUID
    role_on_engagement: AllocationRole
    date_from: str
    date_to: str
    allocation_pct: float = 100
    notes: str | None = None


class ScenarioLineRead(BaseModel):
    id: uuid.UUID
    scenario_id: uuid.UUID
    staff_id: uuid.UUID
    engagement_id: uuid.UUID
    role_on_engagement: str
    date_from: str
    date_to: str
    allocation_pct: float
    notes: str | None
    promoted_allocation_id: uuid.UUID | None
    is_active: bool


class RuleViolationOut(BaseModel):
    code: str
    severity: str
    message: str
    context: dict = {}
    overridable: bool = False
    override_role: str | None = None


class ScenarioLineImpactOut(BaseModel):
    line_id: uuid.UUID
    staff_id: uuid.UUID
    staff_name: str
    engagement_code: str
    client_name: str
    date_from: str
    date_to: str
    allocation_pct: float
    violations: list[RuleViolationOut]


class StaffImpactOut(BaseModel):
    staff_id: uuid.UUID
    staff_name: str
    current_util_pct: float
    added_pct: float
    projected_util_pct: float


class ScenarioImpactOut(BaseModel):
    scenario_id: uuid.UUID
    has_blocking: bool
    lines: list[ScenarioLineImpactOut]
    staff_impact: list[StaffImpactOut]


class ScenarioPromoteResult(BaseModel):
    scenario_id: uuid.UUID
    promoted_count: int
    skipped: list[dict]
