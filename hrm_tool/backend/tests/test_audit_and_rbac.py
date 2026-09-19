"""Acceptance tests T12, T13, T16 (§14)."""
import uuid

from sqlmodel import select

from app.models.audit_log import AuditLog
from app.models.enums import AuditAction, UserRole
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement


def test_t12_one_audit_log_row_per_mutation(client, session):
    make_user(session, UserRole.ADMIN, email="a1@x.com")
    headers = auth_headers(client, "a1@x.com")

    resp = client.post(
        "/api/v1/staff",
        headers=headers,
        json={
            "employee_code": "T12-1", "full_name": "Audit Test", "staff_category": "EMPLOYEE_CA",
            "designation": "ASSOCIATE", "grade_rank": 8,
        },
    )
    assert resp.status_code == 201
    staff_id = resp.json()["id"]

    logs = session.exec(
        select(AuditLog).where(AuditLog.entity_type == "staff").where(AuditLog.entity_id == staff_id).where(AuditLog.action == AuditAction.CREATE)
    ).all()
    assert len(logs) == 1
    assert logs[0].after["employee_code"] == "T12-1"
    assert logs[0].before is None

    update_resp = client.patch(f"/api/v1/staff/{staff_id}", headers=headers, json={"full_name": "Audit Test Updated"})
    assert update_resp.status_code == 200
    update_logs = session.exec(
        select(AuditLog).where(AuditLog.entity_type == "staff").where(AuditLog.entity_id == staff_id).where(AuditLog.action == AuditAction.UPDATE)
    ).all()
    assert len(update_logs) == 1
    assert update_logs[0].before["full_name"] == "Audit Test"
    assert update_logs[0].after["full_name"] == "Audit Test Updated"


def test_t13_manager_sees_null_fee_not_403(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id, fee_amount=5_00_000)

    make_user(session, UserRole.MANAGER, email="mgr@x.com")
    headers = auth_headers(client, "mgr@x.com")

    resp = client.get(f"/api/v1/engagements/{engagement.id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["fee_amount"] is None

    make_user(session, UserRole.PARTNER, email="partner@x.com")
    partner_headers = auth_headers(client, "partner@x.com")
    resp2 = client.get(f"/api/v1/engagements/{engagement.id}", headers=partner_headers)
    assert resp2.json()["fee_amount"] == 500000.0


def test_t16_hard_delete_405_soft_delete_preserves_record(client, session):
    make_user(session, UserRole.ADMIN, email="admin3@x.com")
    headers = auth_headers(client, "admin3@x.com")

    create_resp = client.post(
        "/api/v1/staff",
        headers=headers,
        json={
            "employee_code": "T16-1", "full_name": "Delete Test", "staff_category": "EMPLOYEE_CA",
            "designation": "ASSOCIATE", "grade_rank": 8,
        },
    )
    staff_id = create_resp.json()["id"]

    hard_attempt = client.request("DELETE", f"/api/v1/staff/{staff_id}?hard=true", headers=headers)
    assert hard_attempt.status_code == 405

    soft_delete = client.request("DELETE", f"/api/v1/staff/{staff_id}", headers=headers)
    assert soft_delete.status_code == 200

    from app.models.staff import Staff

    row = session.get(Staff, uuid.UUID(staff_id))
    assert row is not None  # record preserved, not hard-deleted
    assert row.is_active is False
    assert row.deleted_at is not None

    delete_logs = session.exec(
        select(AuditLog).where(AuditLog.entity_type == "staff").where(AuditLog.entity_id == staff_id).where(AuditLog.action == AuditAction.DELETE)
    ).all()
    assert len(delete_logs) == 1
