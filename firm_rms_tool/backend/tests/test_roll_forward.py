"""API coverage for POST /engagements/{id}/roll-forward (P10)."""
from app.models.allocation import Allocation
from app.models.enums import AllocationRole, AllocationStatus
from app.models.enums import Designation, StaffCategory, UserRole
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff


def _headers(client, session, email="rf1@x.com"):
    make_user(session, UserRole.RESOURCE_MANAGER, email=email)
    return auth_headers(client, email)


def _confirmed_allocation(session, engagement, staff, date_from, date_to, role=AllocationRole.TEAM_MEMBER):
    row = Allocation(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=role,
        date_from=date_from, date_to=date_to, allocation_pct=100, status=AllocationStatus.CONFIRMED,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_roll_forward_shifts_dates_and_copies_team(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(
        session, cl.id, dept.id, financial_year="FY2026-27", reporting_deadline="2026-09-30",
        budget_hours_total=200,
    )
    staff = make_staff(session)
    _confirmed_allocation(session, engagement, staff, "2026-09-01", "2026-09-15")
    headers = _headers(client, session)

    resp = client.post(
        f"/api/v1/engagements/{engagement.id}/roll-forward", headers=headers,
        json={"new_engagement_code": f"{engagement.engagement_code}-RF"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    new_eng = body["new_engagement"]
    assert new_eng["financial_year"] == "FY2027-28"
    assert new_eng["reporting_deadline"] == "2027-09-30"
    assert new_eng["budget_hours_total"] == 200
    assert new_eng["status"] == "PIPELINE"
    assert new_eng["prior_year_engagement_id"] == str(engagement.id)
    assert new_eng["fee_amount"] is None

    assert len(body["copied"]) == 1
    assert body["copied"][0]["date_from"] == "2027-09-01"
    assert body["copied"][0]["date_to"] == "2027-09-15"
    assert body["skipped"] == []

    real_allocs = client.get("/api/v1/allocations", headers=headers, params={"staff_id": str(staff.id)}).json()
    new_alloc = next(a for a in real_allocs if a["engagement_id"] == new_eng["id"])
    assert new_alloc["status"] == "DRAFT"
    assert new_alloc["date_from"] == "2027-09-01"


def test_roll_forward_skips_conflicting_line_and_reports_reason(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id, financial_year="FY2026-27")
    staff = make_staff(session)
    _confirmed_allocation(session, engagement, staff, "2026-09-01", "2026-09-10")
    headers = _headers(client, session, "rf2@x.com")

    # Book the staff member solid for the shifted window on a different engagement,
    # so the rolled-forward line collides (OVERALLOCATION).
    other_engagement = make_engagement(session, cl.id, dept.id, financial_year="FY2027-28")
    _confirmed_allocation(session, other_engagement, staff, "2027-09-01", "2027-09-10")

    resp = client.post(
        f"/api/v1/engagements/{engagement.id}/roll-forward", headers=headers,
        json={"new_engagement_code": f"{engagement.engagement_code}-RF2"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["copied"] == []
    assert len(body["skipped"]) == 1
    assert "OVERALLOCATION" in body["skipped"][0]["reasons"][0]


def test_roll_forward_without_copy_team(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id, financial_year="FY2026-27")
    staff = make_staff(session)
    _confirmed_allocation(session, engagement, staff, "2026-09-01", "2026-09-10")
    headers = _headers(client, session, "rf3@x.com")

    resp = client.post(
        f"/api/v1/engagements/{engagement.id}/roll-forward", headers=headers,
        json={"new_engagement_code": f"{engagement.engagement_code}-RF3", "copy_team": False},
    )
    body = resp.json()
    assert body["copied"] == []
    assert body["skipped"] == []


def test_roll_forward_duplicate_code_rejected(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id, financial_year="FY2026-27")
    headers = _headers(client, session, "rf4@x.com")

    resp = client.post(
        f"/api/v1/engagements/{engagement.id}/roll-forward", headers=headers,
        json={"new_engagement_code": engagement.engagement_code},
    )
    assert resp.status_code == 409


def test_roll_forward_requires_write_role(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id, financial_year="FY2026-27")
    make_user(session, UserRole.STAFF, email="rf5@x.com")
    headers = auth_headers(client, "rf5@x.com")

    resp = client.post(
        f"/api/v1/engagements/{engagement.id}/roll-forward", headers=headers,
        json={"new_engagement_code": f"{engagement.engagement_code}-RF5"},
    )
    assert resp.status_code == 403
