"""Unit tests for app/services/actuals.py (P9) — only APPROVED timesheets count."""
from app.models.allocation import Timesheet
from app.services.actuals import engagement_actuals, engagement_margin, staff_actuals
from tests.factories import make_client, make_department, make_engagement, make_staff


def _timesheet(session, staff, engagement, work_date, hours, status="APPROVED", is_chargeable=True):
    row = Timesheet(
        staff_id=staff.id, engagement_id=engagement.id, work_date=work_date, hours=hours,
        is_chargeable=is_chargeable, status=status,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_engagement_actuals_only_counts_approved(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session, cost_rate_per_hour=1000)

    _timesheet(session, staff, engagement, "2026-09-01", 8, status="APPROVED")
    _timesheet(session, staff, engagement, "2026-09-02", 4, status="SUBMITTED")  # not counted
    _timesheet(session, staff, engagement, "2026-09-03", 2, status="DRAFT")  # not counted
    _timesheet(session, staff, engagement, "2026-09-04", 6, status="APPROVED", is_chargeable=False)

    summary = engagement_actuals(session, engagement.id)
    assert summary.actual_hours == 14
    assert summary.actual_chargeable_hours == 8
    assert summary.actual_cost == 14000
    assert summary.entries == 2


def test_staff_actuals_across_engagements(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement1 = make_engagement(session, cl.id, dept.id)
    engagement2 = make_engagement(session, cl.id, dept.id)
    staff = make_staff(session, cost_rate_per_hour=500)

    _timesheet(session, staff, engagement1, "2026-09-01", 8, status="APPROVED")
    _timesheet(session, staff, engagement2, "2026-09-02", 5, status="APPROVED")

    summary = staff_actuals(session, staff.id)
    assert summary.actual_hours == 13
    assert summary.actual_cost == 6500


def test_engagement_margin_computes_amount_and_pct(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(
        session, cl.id, dept.id, fee_amount=100000, out_of_pocket_budget=5000, budget_hours_total=100,
    )
    staff = make_staff(session, cost_rate_per_hour=1000)
    _timesheet(session, staff, engagement, "2026-09-01", 40, status="APPROVED")

    margin = engagement_margin(session, engagement.id)
    assert margin.actual_cost == 40000
    assert margin.margin_amount == 100000 - 40000 - 5000
    assert margin.margin_pct == 55.0
    assert margin.actual_hours == 40
    assert margin.hours_variance_pct == -60.0  # 40 actual vs 100 budgeted


def test_engagement_margin_none_for_missing_engagement(session):
    import uuid
    assert engagement_margin(session, uuid.uuid4()) is None


def test_engagement_margin_handles_no_fee(session):
    dept = make_department(session)
    cl = make_client(session)
    engagement = make_engagement(session, cl.id, dept.id)  # no fee_amount set
    margin = engagement_margin(session, engagement.id)
    assert margin.fee_amount is None
    assert margin.margin_amount is None
    assert margin.margin_pct is None
