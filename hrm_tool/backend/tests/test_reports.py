"""P7: report library RP-01..RP-09 — smoke coverage for every report's JSON/
xlsx/pdf endpoints, plus a couple of targeted correctness checks."""
from app.models.enums import UserRole
from seed.seed_data import seed
from tests.conftest import auth_headers, make_user

REPORT_KEYS = [f"rp0{i}" for i in range(1, 10)] + ["rp10", "rp11", "rp12", "rp13", "rp14"]


def test_list_reports(client, session):
    make_user(session, UserRole.RESOURCE_MANAGER, email="rep0@x.com")
    headers = auth_headers(client, "rep0@x.com")
    resp = client.get("/api/v1/reports", headers=headers)
    assert resp.status_code == 200
    keys = {r["key"] for r in resp.json()}
    assert keys == set(REPORT_KEYS)


def test_every_report_json_xlsx_pdf_smoke(client, session):
    seed(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="rep1@x.com")
    headers = auth_headers(client, "rep1@x.com")
    params = {"date_from": "2026-04-01", "date_to": "2026-06-30"}

    for key in REPORT_KEYS:
        json_resp = client.get(f"/api/v1/reports/{key}", headers=headers, params=params)
        assert json_resp.status_code == 200, f"{key}: {json_resp.text}"
        assert isinstance(json_resp.json(), list)

        xlsx_resp = client.get(f"/api/v1/reports/{key}/export.xlsx", headers=headers, params=params)
        assert xlsx_resp.status_code == 200, f"{key} xlsx: {xlsx_resp.text}"
        assert xlsx_resp.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert len(xlsx_resp.content) > 0

        pdf_resp = client.get(f"/api/v1/reports/{key}/export.pdf", headers=headers, params=params)
        assert pdf_resp.status_code == 200, f"{key} pdf: {pdf_resp.text}"
        assert pdf_resp.headers["content-type"] == "application/pdf"
        assert pdf_resp.content[:4] == b"%PDF"


def test_rp01_deployment_register_has_rows(client, session):
    seed(session)
    make_user(session, UserRole.RESOURCE_MANAGER, email="rep2@x.com")
    headers = auth_headers(client, "rep2@x.com")
    resp = client.get(
        "/api/v1/reports/rp01", headers=headers,
        params={"date_from": "2026-04-01", "date_to": "2026-06-30"},
    )
    body = resp.json()
    assert len(body) > 0
    row = body[0]
    assert set(["staff_name", "engagement_code", "client_name", "role_on_engagement", "days"]).issubset(row.keys())


def test_rp03_matches_capacity_endpoint(client, session):
    from tests.factories import make_client, make_department, make_engagement, make_staff

    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session, standard_hours_per_week=40)
    make_user(session, UserRole.RESOURCE_MANAGER, email="rep3@x.com")
    headers = auth_headers(client, "rep3@x.com")

    client.post(
        "/api/v1/allocations", headers=headers,
        json={
            "engagement_id": str(engagement.id), "staff_id": str(staff.id), "role_on_engagement": "TEAM_MEMBER",
            "date_from": "2026-09-07", "date_to": "2026-09-11", "allocation_pct": 100, "status": "CONFIRMED",
        },
    )
    params = {"date_from": "2026-09-07", "date_to": "2026-09-11"}
    rp03 = client.get("/api/v1/reports/rp03", headers=headers, params=params).json()
    capacity = client.get("/api/v1/capacity/utilisation", headers=headers, params=params).json()

    rp03_row = next(r for r in rp03 if r["full_name"] == staff.full_name)
    cap_row = next(r for r in capacity if r["staff_id"] == str(staff.id))
    assert rp03_row["allocated_hrs"] == cap_row["allocated_hrs"] == 40.0
    assert rp03_row["utilisation_pct"] == cap_row["utilisation_pct"] == 100.0


def test_rp07_surfaces_recorded_override(client, session):
    from tests.factories import make_client, make_department, make_engagement, make_staff

    dept = make_department(session)
    cl = make_client(session, is_pie=False)
    engagement = make_engagement(session, cl.id, dept.id, ep_rotation_due_fy="FY2025-26", financial_year="FY2026-27")
    partner = make_staff(session, staff_category="PARTNER", designation="PARTNER", grade_rank=2, icai_membership_no="777")
    make_user(session, UserRole.RESOURCE_MANAGER, email="rep4@x.com")
    headers = auth_headers(client, "rep4@x.com")

    client.post(
        "/api/v1/allocations", headers=headers,
        json={
            "engagement_id": str(engagement.id), "staff_id": str(partner.id), "role_on_engagement": "ENGAGEMENT_PARTNER",
            "date_from": "2026-09-01", "date_to": "2026-09-30", "allocation_pct": 100, "status": "CONFIRMED",
            "overrides": [{"code": "EP_ROTATION_DUE", "reason": "Interim continuation approved by MP."}],
        },
    )
    resp = client.get(
        "/api/v1/reports/rp07", headers=headers,
        params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    body = resp.json()
    assert any(r["rule_code"] == "EP_ROTATION_DUE" and "MP" in r["reason"] for r in body)


def test_rp09_leave_conflict_count(client, session):
    from app.models.allocation import Allocation, NonAvailability
    from app.models.enums import AllocationRole, AllocationStatus
    from tests.factories import make_client, make_department, make_engagement, make_staff

    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session)
    session.add(
        Allocation(
            engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
            date_from="2026-09-10", date_to="2026-09-12", allocation_pct=100, status=AllocationStatus.CONFIRMED,
        )
    )
    session.add(
        NonAvailability(staff_id=staff.id, type="PRIVILEGE_LEAVE", date_from="2026-09-11", date_to="2026-09-13", status="APPROVED")
    )
    session.commit()

    make_user(session, UserRole.RESOURCE_MANAGER, email="rep5@x.com")
    headers = auth_headers(client, "rep5@x.com")
    resp = client.get(
        "/api/v1/reports/rp09", headers=headers,
        params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    row = next(r for r in resp.json() if r["staff_name"] == staff.full_name)
    assert row["conflicts_caused"] == 1


def test_rp13_independence_and_rotation(client, session):
    from app.models.allocation import IndependenceDeclaration
    from tests.factories import make_client, make_department, make_engagement, make_staff

    dept = make_department(session)
    cl = make_client(session, is_pie=True)
    engagement = make_engagement(
        session, cl.id, dept.id, financial_year="FY2026-27", ep_rotation_due_fy="FY2025-26", first_year_of_appointment=2020,
    )
    partner = make_staff(session, staff_category="PARTNER", designation="PARTNER", grade_rank=2, icai_membership_no="555")
    engagement.engagement_partner_id = partner.id
    session.add(engagement)
    session.add(
        IndependenceDeclaration(
            staff_id=partner.id, client_id=cl.id, declaration_fy="FY2026-27", is_conflicted=True, reviewed_by=partner.id,
        )
    )
    session.commit()

    make_user(session, UserRole.RESOURCE_MANAGER, email="rep6@x.com")
    headers = auth_headers(client, "rep6@x.com")
    resp = client.get(
        "/api/v1/reports/rp13", headers=headers,
        params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["engagement_code"] == engagement.engagement_code)
    assert row["ep_name"] == partner.full_name
    assert row["ep_tenure_years"] == 6
    assert row["ep_rotation_due_fy"] == "FY2025-26"
    assert row["open_conflicts"] == 1
    assert row["declaration_status"] == "Reviewed"
    assert row["is_pie"] is True


def test_rp10_engagement_profitability(client, session):
    from app.models.allocation import Timesheet
    from tests.factories import make_client, make_department, make_engagement, make_staff

    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(
        session, cl.id, dept.id, fee_amount=50000, out_of_pocket_budget=2000, budget_hours_total=40,
    )
    staff = make_staff(session, cost_rate_per_hour=800)
    session.add(Timesheet(staff_id=staff.id, engagement_id=engagement.id, work_date="2026-09-01", hours=20, status="APPROVED"))
    session.commit()

    make_user(session, UserRole.RESOURCE_MANAGER, email="rep7@x.com")
    headers = auth_headers(client, "rep7@x.com")
    resp = client.get(
        "/api/v1/reports/rp10", headers=headers,
        params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["engagement_code"] == engagement.engagement_code)
    assert row["actual_cost"] == 16000
    assert row["margin_amount"] == 50000 - 16000 - 2000
    assert row["actual_hours"] == 20


def test_rp11_timesheet_summary(client, session):
    from app.models.allocation import Timesheet
    from tests.factories import make_client, make_department, make_engagement, make_staff

    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session)
    session.add(Timesheet(staff_id=staff.id, engagement_id=engagement.id, work_date="2026-09-01", hours=8, status="APPROVED"))
    session.add(Timesheet(staff_id=staff.id, engagement_id=engagement.id, work_date="2026-09-02", hours=4, status="SUBMITTED"))
    session.commit()

    make_user(session, UserRole.RESOURCE_MANAGER, email="rep8@x.com")
    headers = auth_headers(client, "rep8@x.com")
    resp = client.get(
        "/api/v1/reports/rp11", headers=headers,
        params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["staff_name"] == staff.full_name)
    assert row["hours_approved"] == 8
    assert row["hours_submitted"] == 4
    assert row["chargeable_hours_approved"] == 8


def test_rp12_capacity_forecast_buckets_by_month(client, session):
    import datetime as dt

    from app.models.capacity import CapacityDaily
    from tests.factories import make_office, make_staff

    office = make_office(session)
    staff = make_staff(session, base_office_id=office.id)
    for day, net, allocated in [
        (dt.date(2026, 9, 15), 8.0, 8.0),
        (dt.date(2026, 9, 16), 8.0, 4.0),
        (dt.date(2026, 10, 1), 8.0, 8.0),
    ]:
        session.add(CapacityDaily(staff_id=staff.id, capacity_date=day, net_capacity_hrs=net, allocated_hrs=allocated))
    session.commit()

    make_user(session, UserRole.RESOURCE_MANAGER, email="rep9@x.com")
    headers = auth_headers(client, "rep9@x.com")
    resp = client.get(
        "/api/v1/reports/rp12", headers=headers,
        params={"date_from": "2026-09-01", "date_to": "2026-10-31", "office_id": str(office.id)},
    )
    assert resp.status_code == 200
    rows = resp.json()
    sep_row = next(r for r in rows if r["month"] == "2026-09")
    oct_row = next(r for r in rows if r["month"] == "2026-10")
    assert sep_row["net_capacity_hrs"] == 16.0
    assert sep_row["allocated_hrs"] == 12.0
    assert sep_row["forecast_utilisation_pct"] == 75.0
    assert oct_row["allocated_hrs"] == 8.0
    assert sep_row["headcount"] == 1


def test_rp14_bench_and_burnout_watchlist(client, session):
    import datetime as dt

    from app.models.capacity import CapacityDaily
    from tests.factories import make_staff

    bench_staff = make_staff(session, full_name="Bench Case")
    burnout_staff = make_staff(session, full_name="Burnout Case")

    # 6 consecutive bench days ending 2026-09-10 (default bench_days threshold = 5)
    for i in range(6):
        session.add(
            CapacityDaily(
                staff_id=bench_staff.id, capacity_date=dt.date(2026, 9, 5) + dt.timedelta(days=i),
                net_capacity_hrs=8.0, allocated_hrs=0.0, bench_flag=True, utilisation_pct=0.0,
            )
        )
    # 7 consecutive weeks at 95% utilisation ending 2026-09-13 (default burnout_weeks threshold = 6)
    start = dt.date(2026, 7, 27)  # a Monday
    for week in range(7):
        for day in range(5):
            session.add(
                CapacityDaily(
                    staff_id=burnout_staff.id, capacity_date=start + dt.timedelta(weeks=week, days=day),
                    net_capacity_hrs=8.0, allocated_hrs=7.6, bench_flag=False, utilisation_pct=95.0,
                )
            )
    session.commit()

    make_user(session, UserRole.RESOURCE_MANAGER, email="rep10@x.com")
    headers = auth_headers(client, "rep10@x.com")
    resp = client.get(
        "/api/v1/reports/rp14", headers=headers,
        params={"date_from": "2026-07-01", "date_to": "2026-09-13"},
    )
    assert resp.status_code == 200
    rows = resp.json()
    bench_row = next(r for r in rows if r["staff_name"] == bench_staff.full_name)
    assert bench_row["watchlist_type"] == "BENCH"
    assert bench_row["consecutive_count"] == 6

    burnout_row = next(r for r in rows if r["staff_name"] == burnout_staff.full_name)
    assert burnout_row["watchlist_type"] == "BURNOUT"
    assert burnout_row["consecutive_count"] == 7
