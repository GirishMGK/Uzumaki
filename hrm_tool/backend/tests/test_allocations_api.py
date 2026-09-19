"""End-to-end API coverage for the /allocations router and /validate endpoint."""
from app.models.enums import UserRole
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff


def _setup(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session)
    return dept, cl, engagement, staff


def test_validate_endpoint_no_violations(client, session):
    _, _, engagement, staff = _setup(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="rm@x.com")
    headers = auth_headers(client, "rm@x.com")

    resp = client.post(
        "/api/v1/allocations/validate",
        headers=headers,
        json={
            "engagement_id": str(engagement.id), "staff_id": str(staff.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-01", "date_to": "2026-09-10", "allocation_pct": 100,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["is_blocked"] is False


def test_create_then_double_book_blocked(client, session):
    _, _, engagement, staff = _setup(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="rm2@x.com")
    headers = auth_headers(client, "rm2@x.com")

    payload = {
        "engagement_id": str(engagement.id), "staff_id": str(staff.id), "role_on_engagement": "TEAM_MEMBER",
        "date_from": "2026-09-01", "date_to": "2026-09-30", "allocation_pct": 100, "status": "CONFIRMED",
    }
    first = client.post("/api/v1/allocations", headers=headers, json=payload)
    assert first.status_code == 201

    second = client.post(
        "/api/v1/allocations", headers=headers,
        json={**payload, "date_from": "2026-09-15", "date_to": "2026-09-20"},
    )
    assert second.status_code == 422
    assert "OVERALLOCATION" in str(second.json())


def test_warn_violation_requires_override_reason(client, session):
    """R8 EP_ROTATION_DUE is WARN for a non-PIE client — must be creatable with a typed override."""
    dept = make_department(session)
    cl = make_client(session, is_pie=False)
    engagement = make_engagement(session, cl.id, dept.id, ep_rotation_due_fy="FY2025-26", financial_year="FY2026-27")
    partner = make_staff(
        session, staff_category="PARTNER", designation="PARTNER", grade_rank=2, icai_membership_no="999999",
    )
    make_user(session, UserRole.RESOURCE_MANAGER, email="rm3@x.com")
    headers = auth_headers(client, "rm3@x.com")

    payload = {
        "engagement_id": str(engagement.id), "staff_id": str(partner.id), "role_on_engagement": "ENGAGEMENT_PARTNER",
        "date_from": "2026-09-01", "date_to": "2026-09-30", "allocation_pct": 100, "status": "CONFIRMED",
    }
    blocked = client.post("/api/v1/allocations", headers=headers, json=payload)
    assert blocked.status_code == 422

    with_override = client.post(
        "/api/v1/allocations", headers=headers,
        json={**payload, "overrides": [{"code": "EP_ROTATION_DUE", "reason": "Successor partner still under KYC; MP approved interim continuation."}]},
    )
    assert with_override.status_code == 201
    assert with_override.json()["override_flags"][0]["code"] == "EP_ROTATION_DUE"
