"""API coverage for /independence-declarations and /resource-requests (P8)."""
from app.models.enums import UserRole
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff


def _setup(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session)
    return dept, cl, engagement, staff


def test_independence_declaration_create_and_review(client, session):
    _, cl, _, staff = _setup(session)
    make_user(session, UserRole.HR, email="hr@x.com")
    hr_headers = auth_headers(client, "hr@x.com")

    created = client.post(
        "/api/v1/independence-declarations",
        headers=hr_headers,
        json={
            "staff_id": str(staff.id), "client_id": str(cl.id), "declaration_fy": "FY2026-27",
            "has_financial_interest": True,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["is_conflicted"] is False  # unreviewed yet
    assert body["declared_on"] is not None

    reviewed = client.post(
        f"/api/v1/independence-declarations/{body['id']}/review",
        headers=hr_headers,
        json={"is_conflicted": True, "notes": "Confirmed shareholding via KYC check."},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["is_conflicted"] is True
    assert reviewed.json()["reviewed_by"] is not None

    listed = client.get(
        "/api/v1/independence-declarations", headers=hr_headers, params={"staff_id": str(staff.id), "is_conflicted": True},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_independence_declaration_requires_write_role(client, session):
    _, cl, _, staff = _setup(session)
    make_user(session, UserRole.VIEWER, email="viewer@x.com")
    headers = auth_headers(client, "viewer@x.com")

    resp = client.post(
        "/api/v1/independence-declarations",
        headers=headers,
        json={"staff_id": str(staff.id), "client_id": str(cl.id), "declaration_fy": "FY2026-27"},
    )
    assert resp.status_code == 403


def test_resource_request_open_then_partially_filled_then_filled(client, session):
    _, _, engagement, staff = _setup(session)
    staff2 = make_staff(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="rm4@x.com")
    headers = auth_headers(client, "rm4@x.com")

    created = client.post(
        "/api/v1/resource-requests",
        headers=headers,
        json={
            "engagement_id": str(engagement.id), "date_from": "2026-09-01", "date_to": "2026-09-30",
            "headcount": 2, "required_grade": "ASSOCIATE",
        },
    )
    assert created.status_code == 201
    req = created.json()
    assert req["status"] == "OPEN"
    assert req["requested_by"] is not None

    alloc1 = client.post(
        "/api/v1/allocations", headers=headers,
        json={
            "engagement_id": str(engagement.id), "staff_id": str(staff.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-01", "date_to": "2026-09-30", "allocation_pct": 100, "status": "CONFIRMED",
        },
    )
    assert alloc1.status_code == 201

    fulfil1 = client.post(
        f"/api/v1/resource-requests/{req['id']}/fulfil", headers=headers,
        json={"allocation_id": alloc1.json()["id"]},
    )
    assert fulfil1.status_code == 200
    assert fulfil1.json()["status"] == "PARTIALLY_FILLED"

    alloc2 = client.post(
        "/api/v1/allocations", headers=headers,
        json={
            "engagement_id": str(engagement.id), "staff_id": str(staff2.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-01", "date_to": "2026-09-30", "allocation_pct": 100, "status": "CONFIRMED",
        },
    )
    assert alloc2.status_code == 201

    fulfil2 = client.post(
        f"/api/v1/resource-requests/{req['id']}/fulfil", headers=headers,
        json={"allocation_id": alloc2.json()["id"]},
    )
    assert fulfil2.status_code == 200
    assert fulfil2.json()["status"] == "FILLED"
    assert len(fulfil2.json()["fulfilment_allocation_ids"]) == 2


def test_resource_request_fulfil_rejects_allocation_from_other_engagement(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    other_engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="rm5@x.com")
    headers = auth_headers(client, "rm5@x.com")

    req = client.post(
        "/api/v1/resource-requests", headers=headers,
        json={"engagement_id": str(engagement.id), "date_from": "2026-09-01", "date_to": "2026-09-30"},
    ).json()
    alloc = client.post(
        "/api/v1/allocations", headers=headers,
        json={
            "engagement_id": str(other_engagement.id), "staff_id": str(staff.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-01", "date_to": "2026-09-30", "allocation_pct": 100, "status": "CONFIRMED",
        },
    ).json()

    resp = client.post(f"/api/v1/resource-requests/{req['id']}/fulfil", headers=headers, json={"allocation_id": alloc["id"]})
    assert resp.status_code == 422


def test_resource_request_reject(client, session):
    _, _, engagement, _ = _setup(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="rm6@x.com")
    headers = auth_headers(client, "rm6@x.com")

    req = client.post(
        "/api/v1/resource-requests", headers=headers,
        json={"engagement_id": str(engagement.id), "date_from": "2026-09-01", "date_to": "2026-09-30"},
    ).json()
    resp = client.post(f"/api/v1/resource-requests/{req['id']}/reject", headers=headers, params={"reason": "No bench capacity"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"
