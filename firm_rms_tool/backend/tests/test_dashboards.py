"""Acceptance tests T9, T10 (§14) plus general coverage for C1-C6 + export."""
import uuid

from app.models.allocation import Allocation
from app.models.enums import AllocationRole, AllocationStatus, UserRole
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff
from seed.seed_data import seed


def test_t9_c1_headcount_reconciles_to_raw_query(client, session):
    seed(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="dash1@x.com")
    headers = auth_headers(client, "dash1@x.com")

    resp = client.get("/api/v1/dashboards/c1-headcount", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    total_from_chart = sum(r["count"] for r in body)

    from sqlmodel import func, select
    from app.models.staff import Staff

    total_active_staff = session.exec(select(func.count()).select_from(Staff).where(Staff.is_active == True)).one()  # noqa: E712
    assert total_from_chart == total_active_staff == 300

    # Spot-check one (office, category) bucket against a raw GROUP BY.
    office_id, category = body[0]["office_id"], body[0]["staff_category"]
    raw_count = session.exec(
        select(func.count()).select_from(Staff)
        .where(Staff.is_active == True)  # noqa: E712
        .where(Staff.base_office_id == uuid.UUID(office_id))
        .where(Staff.staff_category == category)
    ).one()
    assert body[0]["count"] == raw_count


def test_t10_c3_partner_fte_50_50_split_never_double_counts(client, session):
    dept = make_department(session)
    cl = make_client(session)
    partner_a = make_staff(session, staff_category="PARTNER", designation="PARTNER", grade_rank=2, icai_membership_no="1")
    partner_b = make_staff(session, staff_category="PARTNER", designation="PARTNER", grade_rank=2, icai_membership_no="2")
    staff = make_staff(session, designation="MANAGER", grade_rank=5)

    eng_a = make_engagement(session, cl.id, dept.id, engagement_partner_id=partner_a.id, engagement_code="ENG-A")
    eng_b = make_engagement(session, cl.id, dept.id, engagement_partner_id=partner_b.id, engagement_code="ENG-B")

    for eng, partner in ((eng_a, partner_a), (eng_b, partner_b)):
        session.add(
            Allocation(
                engagement_id=eng.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
                partner_id=partner.id, date_from="2026-09-01", date_to="2026-09-30", allocation_pct=50,
                status=AllocationStatus.CONFIRMED,
            )
        )
    session.commit()

    make_user(session, UserRole.RESOURCE_MANAGER, email="dash2@x.com")
    headers = auth_headers(client, "dash2@x.com")
    resp = client.get(
        "/api/v1/dashboards/c3-partner-fte", headers=headers,
        params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    assert resp.status_code == 200
    body = resp.json()
    fte_a = next(r["fte"] for r in body if r["partner_id"] == str(partner_a.id))
    fte_b = next(r["fte"] for r in body if r["partner_id"] == str(partner_b.id))
    assert fte_a == 0.5
    assert fte_b == 0.5
    assert fte_a + fte_b == 1.0  # never 1.0 against each = 2.0 total


def test_c4_c5_c6_and_drill_and_export_smoke(client, session):
    seed(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="dash3@x.com")
    headers = auth_headers(client, "dash3@x.com")
    params = {"date_from": "2026-04-01", "date_to": "2026-06-30"}

    c4 = client.get("/api/v1/dashboards/c4-partner-portfolio", headers=headers, params=params)
    assert c4.status_code == 200 and len(c4.json()) > 0
    assert "fee_under_management" in c4.json()[0]

    c5 = client.get("/api/v1/dashboards/c5-department-fte", headers=headers, params=params)
    assert c5.status_code == 200
    assert "current" in c5.json() and "trend" in c5.json()
    assert len(c5.json()["trend"]) > 0

    c6 = client.get("/api/v1/dashboards/c6-department-grade", headers=headers, params=params)
    assert c6.status_code == 200 and len(c6.json()) > 0

    drill = client.get("/api/v1/dashboards/drill", headers=headers, params=params)
    assert drill.status_code == 200 and len(drill.json()) > 0

    export = client.get("/api/v1/dashboards/c3/export", headers=headers, params=params)
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert len(export.content) > 0


def test_dashboard_endpoints_respect_department_filter(client, session):
    seed(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="dash4@x.com")
    headers = auth_headers(client, "dash4@x.com")

    from sqlmodel import select
    from app.models.reference import Department

    dept = session.exec(select(Department)).first()

    unfiltered = client.get("/api/v1/dashboards/c1-headcount", headers=headers).json()
    filtered = client.get("/api/v1/dashboards/c6-department-grade", headers=headers, params={
        "date_from": "2026-04-01", "date_to": "2026-06-30", "department_id": str(dept.id),
    }).json()
    assert all(r["department_id"] == str(dept.id) for r in filtered)
    assert sum(r["count"] for r in unfiltered) == 300
