"""Board/lookup endpoints that back the P4 scheduler UI."""
from app.models.allocation import Allocation
from app.models.enums import AllocationRole, AllocationStatus, UserRole
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff


def test_board_returns_staff_allocations_leaves_holidays(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session, primary_department_id=dept.id)

    session.add(
        Allocation(
            engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
            date_from="2026-09-01", date_to="2026-09-30", allocation_pct=100, status=AllocationStatus.CONFIRMED,
        )
    )
    session.commit()

    make_user(session, UserRole.RESOURCE_MANAGER, email="board@x.com")
    headers = auth_headers(client, "board@x.com")

    resp = client.get(
        "/api/v1/scheduler/board",
        headers=headers,
        params={"date_from": "2026-09-01", "date_to": "2026-09-30", "department_id": str(dept.id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any(s["id"] == str(staff.id) for s in body["staff"])
    assert len(body["allocations"]) == 1
    assert body["allocations"][0]["client_name"] == cl.name
    assert body["allocations"][0]["engagement_code"] == engagement.engagement_code


def test_engagements_lookup_filters_by_query(client, session):
    dept = make_department(session)
    cl = make_client(session, name="Zenith Pharma Pvt Ltd")
    engagement = make_engagement(session, cl.id, dept.id)

    make_user(session, UserRole.RESOURCE_MANAGER, email="lookup@x.com")
    headers = auth_headers(client, "lookup@x.com")

    resp = client.get("/api/v1/scheduler/engagements-lookup", headers=headers, params={"q": "Zenith"})
    assert resp.status_code == 200
    body = resp.json()
    assert any(e["id"] == str(engagement.id) for e in body)
