"""Get-or-create lookups the bulk importers need for name-based columns
(Work location, Partner responsible, Group) — the server-side twin of the
same-purpose resolvers in frontend/src/lib/mastersApi.ts used by the
Add-one form. Both exist because the two entry points can't share code
across languages; keeping the *behaviour* identical (resolve by
case-insensitive name, create on first use) is what actually matters, not
a shared implementation.
"""
import re
import uuid
from datetime import date

from sqlmodel import Session, select

from app.importers.friendly_values import CITY_STATE
from app.models.client import ClientGroup
from app.models.enums import Designation, EmploymentStatus, StaffCategory
from app.models.reference import Office
from app.models.staff import Staff


def resolve_office_id(db: Session, city: str, actor_id: uuid.UUID) -> uuid.UUID:
    existing = db.exec(select(Office).where(Office.name.ilike(city))).first()
    if existing:
        return existing.id
    office = Office(
        code=city[:3].upper(), name=city, city=city, state=CITY_STATE.get(city, city),
        created_by=actor_id, updated_by=actor_id,
    )
    db.add(office)
    db.flush()
    return office.id


def resolve_partner_id(db: Session, full_name: str, actor_id: uuid.UUID) -> uuid.UUID:
    existing = db.exec(select(Staff).where(Staff.full_name.ilike(full_name))).first()
    if existing:
        return existing.id
    code_prefix = re.sub(r"[^A-Za-z]", "", full_name)[:4].upper() or "PTR"
    employee_code = f"PTR-{code_prefix}-{uuid.uuid4().hex[:6].upper()}"
    staff = Staff(
        employee_code=employee_code, full_name=full_name,
        staff_category=StaffCategory.PARTNER, designation=Designation.PARTNER, grade_rank=2,
        employment_status=EmploymentStatus.ACTIVE,
        created_by=actor_id, updated_by=actor_id,
    )
    db.add(staff)
    db.flush()
    return staff.id


def resolve_group_id(db: Session, name: str, actor_id: uuid.UUID) -> uuid.UUID | None:
    name = (name or "").strip()
    if not name:
        return None
    existing = db.exec(select(ClientGroup).where(ClientGroup.group_name.ilike(name))).first()
    if existing:
        return existing.id
    code_prefix = re.sub(r"[^A-Za-z0-9]", "", name)[:8].upper() or "GRP"
    group_code = f"GRP-{code_prefix}-{uuid.uuid4().hex[:6].upper()}"
    group = ClientGroup(group_code=group_code, group_name=name, created_by=actor_id, updated_by=actor_id)
    db.add(group)
    db.flush()
    return group.id


def today_iso() -> str:
    return date.today().isoformat()
