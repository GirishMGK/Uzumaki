"""Phase P5: capacity_daily materialization, invalidation, and the perf DoD
("utilisation for 300 staff x 90 days computes in < 2s from the
materialised table")."""
import time
from datetime import date

from sqlmodel import select

from app.models.allocation import HolidayCalendar, NonAvailability
from app.models.capacity import CapacityDaily
from app.models.enums import UserRole
from app.services.capacity_materializer import recompute_range
from app.services.capacity_report import get_staff_utilisation
from seed.seed_data import seed
from tests.conftest import auth_headers, make_user
from tests.factories import make_client, make_department, make_engagement, make_staff


def test_recompute_matches_t8_net_capacity(session):
    """Same scenario as T8 (§14), now going through the materializer."""
    staff = make_staff(session, standard_hours_per_week=40)
    session.add(NonAvailability(staff_id=staff.id, type="PRIVILEGE_LEAVE", date_from="2026-09-07", date_to="2026-09-08", status="APPROVED"))
    session.add(HolidayCalendar(office_id=None, holiday_date="2026-09-09", name="Ganesh Chaturthi"))
    session.commit()

    written = recompute_range(session, date(2026, 9, 7), date(2026, 9, 11), staff_ids=[staff.id])
    assert written == 5  # 5 calendar days in range

    rows = session.exec(select(CapacityDaily).where(CapacityDaily.staff_id == staff.id).order_by(CapacityDaily.capacity_date)).all()
    net_total = sum(r.net_capacity_hrs for r in rows)
    assert net_total == 16.0


def test_recompute_computes_allocated_and_bench(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session, standard_hours_per_week=40)

    from app.models.allocation import Allocation
    from app.models.enums import AllocationRole, AllocationStatus

    session.add(
        Allocation(
            engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
            date_from="2026-09-07", date_to="2026-09-08", allocation_pct=100, status=AllocationStatus.CONFIRMED,
            is_chargeable=True,
        )
    )
    session.commit()

    recompute_range(session, date(2026, 9, 7), date(2026, 9, 11), staff_ids=[staff.id])
    rows = {r.capacity_date: r for r in session.exec(select(CapacityDaily).where(CapacityDaily.staff_id == staff.id)).all()}

    assert rows[date(2026, 9, 7)].allocated_hrs == 8.0
    assert rows[date(2026, 9, 7)].chargeable_hrs == 8.0
    assert rows[date(2026, 9, 7)].bench_flag is False
    # 9th-11th (Wed-Fri) have zero bookings -> should trend toward bench after enough consecutive free days
    assert rows[date(2026, 9, 11)].available_hrs == 8.0


def test_invalidation_on_allocation_create_and_cancel(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session, standard_hours_per_week=40)
    make_user(session, UserRole.RESOURCE_MANAGER, email="cap-rm@x.com")
    headers = auth_headers(client, "cap-rm@x.com")

    create_resp = client.post(
        "/api/v1/allocations", headers=headers,
        json={
            "engagement_id": str(engagement.id), "staff_id": str(staff.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-07", "date_to": "2026-09-11", "allocation_pct": 100, "status": "CONFIRMED",
        },
    )
    assert create_resp.status_code == 201
    alloc_id = create_resp.json()["id"]

    row = session.exec(
        select(CapacityDaily).where(CapacityDaily.staff_id == staff.id).where(CapacityDaily.capacity_date == date(2026, 9, 7))
    ).first()
    assert row is not None
    assert row.allocated_hrs == 8.0

    cancel_resp = client.request("DELETE", f"/api/v1/allocations/{alloc_id}", headers=headers, params={"reason": "test"})
    assert cancel_resp.status_code == 200

    session.expire_all()
    row_after = session.exec(
        select(CapacityDaily).where(CapacityDaily.staff_id == staff.id).where(CapacityDaily.capacity_date == date(2026, 9, 7))
    ).first()
    assert row_after.allocated_hrs == 0.0


def test_utilisation_endpoint_reads_materialized_table(client, session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session, standard_hours_per_week=40, primary_department_id=dept.id)
    make_user(session, UserRole.RESOURCE_MANAGER, email="cap-rm2@x.com")
    headers = auth_headers(client, "cap-rm2@x.com")

    client.post(
        "/api/v1/allocations", headers=headers,
        json={
            "engagement_id": str(engagement.id), "staff_id": str(staff.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-07", "date_to": "2026-09-11", "allocation_pct": 100, "status": "CONFIRMED",
        },
    )
    resp = client.get(
        "/api/v1/capacity/utilisation", headers=headers,
        params={"date_from": "2026-09-07", "date_to": "2026-09-11"},
    )
    assert resp.status_code == 200
    body = resp.json()
    row = next(r for r in body if r["staff_id"] == str(staff.id))
    assert row["allocated_hrs"] == 40.0
    assert row["utilisation_pct"] == 100.0


def test_p5_dod_perf_300_staff_90_days(session):
    """DoD: 'Utilisation for 300 staff x 90 days computes in < 2s from the materialised table.'"""
    seed(session)

    d_from, d_to = date(2026, 4, 1), date(2026, 6, 29)  # 90 days
    t0 = time.perf_counter()
    written = recompute_range(session, d_from, d_to)
    materialize_elapsed = time.perf_counter() - t0
    assert written > 0

    t1 = time.perf_counter()
    rows = get_staff_utilisation(session, d_from, d_to)
    query_elapsed = time.perf_counter() - t1

    assert len(rows) == 300
    assert query_elapsed < 2.0, f"utilisation query took {query_elapsed:.2f}s"
    # Materialization itself isn't in the DoD's timing (that's the nightly
    # job's job), but it should still be sane for a single test run.
    assert materialize_elapsed < 10.0, f"materialize took {materialize_elapsed:.2f}s"
