"""Lightweight direct-to-DB factories for test fixtures (bypasses the API)."""
import uuid

from sqlmodel import Session

from app.models.client import Client, ClientGroup
from app.models.engagement import Engagement
from app.models.enums import (
    Designation,
    EntityClass,
    RelationshipStatus,
    RiskRating,
    ServiceType,
    StaffCategory,
)
from app.models.reference import Department, Office
from app.models.staff import Staff


def make_office(session: Session, **kw) -> Office:
    defaults = dict(code=f"OFF-{uuid.uuid4().hex[:6]}", name="Test Office", city="Hyderabad", state="Telangana")
    defaults.update(kw)
    row = Office(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def make_department(session: Session, **kw) -> Department:
    defaults = dict(code=f"DEPT-{uuid.uuid4().hex[:6]}", name="Statutory Audit")
    defaults.update(kw)
    row = Department(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def make_client_group(session: Session, **kw) -> ClientGroup:
    defaults = dict(group_code=f"GRP-{uuid.uuid4().hex[:6]}", group_name="Test Group")
    defaults.update(kw)
    row = ClientGroup(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def make_client(session: Session, **kw) -> Client:
    defaults = dict(
        client_code=f"CL-{uuid.uuid4().hex[:6]}",
        name="Test Client Pvt Ltd",
        entity_class=EntityClass.PRIVATE,
        relationship_status=RelationshipStatus.ACTIVE,
        risk_rating=RiskRating.MEDIUM,
    )
    defaults.update(kw)
    row = Client(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def make_staff(session: Session, **kw) -> Staff:
    defaults = dict(
        employee_code=f"EMP-{uuid.uuid4().hex[:6]}",
        full_name="Test Staff",
        staff_category=StaffCategory.EMPLOYEE_CA,
        designation=Designation.SENIOR_ASSOCIATE,
        grade_rank=7,
        date_of_joining="2020-01-01",
        standard_hours_per_week=40,
    )
    defaults.update(kw)
    row = Staff(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def make_engagement(session: Session, client_id, department_id, **kw) -> Engagement:
    defaults = dict(
        engagement_code=f"ENG-{uuid.uuid4().hex[:6]}",
        client_id=client_id,
        department_id=department_id,
        service_type=ServiceType.STATUTORY_AUDIT,
        financial_year="FY2026-27",
    )
    defaults.update(kw)
    row = Engagement(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
