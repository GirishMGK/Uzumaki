import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.deps import get_current_user, get_db, require_roles
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.capacity import RecomputeResponse, StaffUtilisation
from app.services.capacity_materializer import recompute_range
from app.services.capacity_report import get_staff_utilisation

router = APIRouter()


@router.get("/utilisation", response_model=list[StaffUtilisation])
def utilisation(
    date_from: date,
    date_to: date,
    office_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[StaffUtilisation]:
    """RP-03-lite: reads only capacity_daily (§5) — never raw allocations."""
    rows = get_staff_utilisation(db, date_from, date_to, office_id=office_id, department_id=department_id)
    return [StaffUtilisation(**r) for r in rows]


@router.post("/recompute", response_model=RecomputeResponse)
def manual_recompute(
    date_from: date,
    date_to: date,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.RESOURCE_MANAGER)),
) -> RecomputeResponse:
    """Manual trigger for the same recompute the nightly job runs — useful
    after a bulk import or a data-fix that bypassed the normal mutation
    routes (and their synchronous invalidation).
    """
    rows_written = recompute_range(db, date_from, date_to)
    return RecomputeResponse(rows_written=rows_written, date_from=date_from.isoformat(), date_to=date_to.isoformat())
