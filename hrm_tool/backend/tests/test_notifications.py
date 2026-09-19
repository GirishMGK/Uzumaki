"""Notification service (§9) — no-op-when-unconfigured behaviour, plus
confirming the allocation approve/cancel routes call it without raising
even though the test settings never configure SMTP."""
from app.models.enums import UserRole
from app.services.notifications import notify_allocation_cancelled, notify_allocation_confirmed, send_notification
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff


def test_send_notification_is_a_noop_without_smtp_configured():
    assert send_notification("someone@firm.local", "subject", "body") is False


def test_notify_helpers_are_noop_without_recipient():
    assert notify_allocation_confirmed(None, "Staff", "ENG-1", "Client", "2026-09-01", "2026-09-05") is False
    assert notify_allocation_cancelled(None, "Staff", "ENG-1", "Client", "2026-09-01", "2026-09-05") is False


def test_approve_and_cancel_allocation_do_not_raise_without_smtp(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session, official_email="staff@firm.local")
    make_user(session, UserRole.RESOURCE_MANAGER, email="notif1@x.com")
    headers = auth_headers(client, "notif1@x.com")

    created = client.post(
        "/api/v1/allocations", headers=headers,
        json={
            "engagement_id": str(engagement.id), "staff_id": str(staff.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-01", "date_to": "2026-09-05", "allocation_pct": 100, "status": "DRAFT",
        },
    )
    assert created.status_code == 201
    alloc_id = created.json()["id"]

    make_user(session, UserRole.PARTNER, email="approver1@x.com", staff_id=staff.id)
    approver_headers = auth_headers(client, "approver1@x.com")
    approved = client.post(f"/api/v1/allocations/{alloc_id}/approve", headers=approver_headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "CONFIRMED"

    cancelled = client.delete(f"/api/v1/allocations/{alloc_id}", headers=headers, params={"reason": "Client deferred engagement"})
    assert cancelled.status_code == 200
