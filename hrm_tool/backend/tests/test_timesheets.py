"""API coverage for /timesheets — CRUD + DRAFT -> SUBMITTED -> APPROVED/REJECTED (P9)."""
from app.models.enums import UserRole
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff


def _setup(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session)
    return dept, cl, engagement, staff


def test_self_service_create_submit_approve(client, session):
    _, _, engagement, staff = _setup(session)
    staff_user = make_user(session, UserRole.STAFF, email="ts1@x.com", staff_id=staff.id)
    staff_headers = auth_headers(client, "ts1@x.com")

    created = client.post(
        "/api/v1/timesheets", headers=staff_headers,
        json={"staff_id": str(staff.id), "engagement_id": str(engagement.id), "work_date": "2026-09-01", "hours": 8},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "DRAFT"
    ts_id = body["id"]

    # editing while DRAFT is fine
    edited = client.patch(f"/api/v1/timesheets/{ts_id}", headers=staff_headers, json={"hours": 7.5})
    assert edited.status_code == 200
    assert edited.json()["hours"] == 7.5

    submitted = client.post(f"/api/v1/timesheets/{ts_id}/submit", headers=staff_headers)
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "SUBMITTED"

    # can no longer edit once submitted
    blocked_edit = client.patch(f"/api/v1/timesheets/{ts_id}", headers=staff_headers, json={"hours": 6})
    assert blocked_edit.status_code == 422

    # staff cannot approve their own timesheet
    self_approve = client.post(f"/api/v1/timesheets/{ts_id}/approve", headers=staff_headers)
    assert self_approve.status_code == 403

    make_user(session, UserRole.MANAGER, email="mgr1@x.com")
    mgr_headers = auth_headers(client, "mgr1@x.com")
    approved = client.post(f"/api/v1/timesheets/{ts_id}/approve", headers=mgr_headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["approved_by"] is not None


def test_staff_cannot_log_time_for_someone_else(client, session):
    _, _, engagement, staff = _setup(session)
    other_staff = make_staff(session)
    make_user(session, UserRole.STAFF, email="ts2@x.com", staff_id=staff.id)
    headers = auth_headers(client, "ts2@x.com")

    resp = client.post(
        "/api/v1/timesheets", headers=headers,
        json={"staff_id": str(other_staff.id), "engagement_id": str(engagement.id), "work_date": "2026-09-01", "hours": 8},
    )
    assert resp.status_code == 403


def test_privileged_role_can_log_on_behalf_of_anyone(client, session):
    _, _, engagement, staff = _setup(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="ts3@x.com")
    headers = auth_headers(client, "ts3@x.com")

    resp = client.post(
        "/api/v1/timesheets", headers=headers,
        json={"staff_id": str(staff.id), "engagement_id": str(engagement.id), "work_date": "2026-09-01", "hours": 8},
    )
    assert resp.status_code == 201


def test_reject_records_reason_and_status(client, session):
    _, _, engagement, staff = _setup(session)
    make_user(session, UserRole.STAFF, email="ts4@x.com", staff_id=staff.id)
    staff_headers = auth_headers(client, "ts4@x.com")

    created = client.post(
        "/api/v1/timesheets", headers=staff_headers,
        json={"staff_id": str(staff.id), "engagement_id": str(engagement.id), "work_date": "2026-09-01", "hours": 8},
    ).json()
    client.post(f"/api/v1/timesheets/{created['id']}/submit", headers=staff_headers)

    make_user(session, UserRole.PARTNER, email="partner1@x.com")
    partner_headers = auth_headers(client, "partner1@x.com")
    rejected = client.post(
        f"/api/v1/timesheets/{created['id']}/reject", headers=partner_headers, params={"reason": "Wrong engagement code"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert "Wrong engagement code" in rejected.json()["narration"]


def test_negative_hours_rejected(client, session):
    _, _, engagement, staff = _setup(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="ts5@x.com")
    headers = auth_headers(client, "ts5@x.com")

    resp = client.post(
        "/api/v1/timesheets", headers=headers,
        json={"staff_id": str(staff.id), "engagement_id": str(engagement.id), "work_date": "2026-09-01", "hours": -1},
    )
    assert resp.status_code == 422


def test_list_scopes_self_service_role_to_own_rows(client, session):
    _, _, engagement, staff = _setup(session)
    other_staff = make_staff(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="ts6@x.com")
    rm_headers = auth_headers(client, "ts6@x.com")
    client.post(
        "/api/v1/timesheets", headers=rm_headers,
        json={"staff_id": str(staff.id), "engagement_id": str(engagement.id), "work_date": "2026-09-01", "hours": 8},
    )
    client.post(
        "/api/v1/timesheets", headers=rm_headers,
        json={"staff_id": str(other_staff.id), "engagement_id": str(engagement.id), "work_date": "2026-09-01", "hours": 8},
    )

    make_user(session, UserRole.STAFF, email="ts7@x.com", staff_id=staff.id)
    staff_headers = auth_headers(client, "ts7@x.com")
    listed = client.get("/api/v1/timesheets", headers=staff_headers)
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["staff_id"] == str(staff.id)
