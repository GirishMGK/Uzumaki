"""Smoke test: seed.seed_data.seed() populates the scale described in §14."""
from sqlmodel import func, select

from app.models.client import Client, ClientGroup
from app.models.engagement import Engagement
from app.models.enums import StaffCategory
from app.models.reference import Department, Office
from app.models.staff import Staff
from seed.seed_data import seed


def test_seed_produces_spec_scale(session):
    seed(session)

    assert session.exec(select(func.count()).select_from(Office)).one() == 6
    assert session.exec(select(func.count()).select_from(Department)).one() == 10
    assert session.exec(select(func.count()).select_from(Staff)).one() == 300
    assert session.exec(select(func.count()).select_from(Staff).where(Staff.staff_category == StaffCategory.ARTICLED_ASSISTANT)).one() == 150
    assert session.exec(select(func.count()).select_from(ClientGroup)).one() == 25
    assert session.exec(select(func.count()).select_from(Client)).one() == 200
    assert session.exec(select(func.count()).select_from(Engagement)).one() == 380
