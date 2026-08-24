import os
import uuid

os.environ.setdefault("RMS_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("RMS_JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("RMS_ENABLE_BACKGROUND_JOBS", "false")

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.core.deps import get_db
from app.core.security import hash_password
from app.db import base  # noqa: F401  populate metadata
from app.main import app
from app.models.enums import UserRole
from app.models.user import User


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture()
def client(engine):
    def _get_db_override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def make_user(session: Session, role: UserRole, *, email: str | None = None, staff_id=None) -> User:
    email = email or f"{role.value.lower()}-{uuid.uuid4().hex[:8]}@firm.local"
    user = User(email=email, hashed_password=hash_password("password123"), role=role, full_name=f"Test {role.value}", staff_id=staff_id)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def auth_headers(client: TestClient, email: str, password: str = "password123") -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
