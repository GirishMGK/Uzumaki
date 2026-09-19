"""RP-01..RP-09 report library (§11) — JSON, Excel and PDF, one route family.

Standard params per §11: date range, office, department, partner, client
group, staff category, status. Every report function in
app/services/reports.py takes the same `ReportFilters` shape, so this
router is one small dispatch table rather than nine bespoke routers.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.reports.excel_export import ColumnSpec, build_formatted_workbook
from app.reports.pdf_export import build_formatted_pdf
from app.services import reports as rpt

router = APIRouter()


def _filters(
    office_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None,
    client_group_id: uuid.UUID | None = None,
    staff_category: str | None = None,
    status: str | None = None,
) -> rpt.ReportFilters:
    return rpt.ReportFilters(
        office_id=office_id, department_id=department_id, partner_id=partner_id,
        client_group_id=client_group_id, staff_category=staff_category, status=status,
    )


_REPORTS: dict[str, tuple[str, callable, list[ColumnSpec]]] = {
    "rp01": (
        "Deployment Register", rpt.deployment_register,
        [
            ColumnSpec("staff_name", "Staff"), ColumnSpec("employee_code", "Code"), ColumnSpec("grade", "Grade"),
            ColumnSpec("office_name", "Office"), ColumnSpec("client_name", "Client"),
            ColumnSpec("engagement_code", "Engagement"), ColumnSpec("service_type", "Service Type"),
            ColumnSpec("department_name", "Department"), ColumnSpec("role_on_engagement", "Role"),
            ColumnSpec("reporting_manager", "Reporting Manager"), ColumnSpec("partner_name", "Partner"),
            ColumnSpec("date_from", "From"), ColumnSpec("date_to", "To"), ColumnSpec("days", "Days", "number"),
            ColumnSpec("allocation_pct", "%", "number"), ColumnSpec("planned_hours", "Planned Hrs", "number"),
            ColumnSpec("status", "Status"),
        ],
    ),
    "rp02": (
        "Engagement Team Composition", rpt.engagement_team_composition,
        [
            ColumnSpec("engagement_code", "Engagement"), ColumnSpec("client_name", "Client"),
            ColumnSpec("partner_name", "Partner"), ColumnSpec("eqcr_name", "EQCR"),
            ColumnSpec("manager_name", "Manager"), ColumnSpec("team_by_grade", "Team by Grade"),
            ColumnSpec("team_size", "Team Size", "number"), ColumnSpec("budget_hours_total", "Budget Hrs", "number"),
            ColumnSpec("planned_hours_total", "Planned Hrs", "number"), ColumnSpec("skill_coverage", "Skill Coverage"),
            ColumnSpec("status", "Status"),
        ],
    ),
    "rp03": (
        "Staff Utilisation", rpt.staff_utilisation,
        [
            ColumnSpec("full_name", "Staff"), ColumnSpec("designation", "Grade"),
            ColumnSpec("net_capacity_hrs", "Capacity Hrs", "number"), ColumnSpec("allocated_hrs", "Allocated Hrs", "number"),
            ColumnSpec("chargeable_hrs", "Chargeable Hrs", "number"), ColumnSpec("utilisation_pct", "Util %", "pct"),
            ColumnSpec("chargeable_util_pct", "Chargeable Util %", "pct"),
            ColumnSpec("target_chargeability_pct", "Target %", "number"), ColumnSpec("variance_pct", "Variance", "number"),
            ColumnSpec("bench_days", "Bench Days", "number"),
        ],
    ),
    "rp04": (
        "Partner Portfolio", rpt.partner_portfolio,
        [
            ColumnSpec("partner_name", "Partner"), ColumnSpec("clients", "Clients", "number"),
            ColumnSpec("engagements", "Engagements", "number"), ColumnSpec("fee_under_management", "Fee Under Mgmt", "money"),
            ColumnSpec("fte_deployed", "FTE Deployed", "number"), ColumnSpec("fee_per_fte", "Fee per FTE", "money"),
            ColumnSpec("avg_risk_rating", "Avg Risk", "number"), ColumnSpec("overdue_reports", "Overdue Reports", "number"),
        ],
    ),
    "rp05": (
        "Location/Office Resourcing", rpt.office_resourcing,
        [
            ColumnSpec("office_name", "Office"), ColumnSpec("headcount", "Headcount", "number"),
            ColumnSpec("headcount_by_category", "By Category"), ColumnSpec("fte_deployed", "FTE Deployed", "number"),
            ColumnSpec("inbound_deputation", "Inbound", "number"), ColumnSpec("outbound_deputation", "Outbound", "number"),
            ColumnSpec("avg_utilisation_pct", "Avg Util %", "pct"),
        ],
    ),
    "rp06": (
        "Bench and Availability", rpt.bench_and_availability,
        [
            ColumnSpec("staff_name", "Staff"), ColumnSpec("employee_code", "Code"),
            ColumnSpec("available_from", "Available From"), ColumnSpec("available_days", "Available Days", "number"),
            ColumnSpec("grade", "Grade"), ColumnSpec("skills", "Skills"), ColumnSpec("base_office", "Base Office"),
            ColumnSpec("last_engagement", "Last Engagement"),
        ],
    ),
    "rp07": (
        "Conflict and Exception Report", rpt.conflict_and_exception_report,
        [
            ColumnSpec("staff_name", "Staff"), ColumnSpec("engagement_code", "Engagement"),
            ColumnSpec("client_name", "Client"), ColumnSpec("rule_code", "Rule"), ColumnSpec("reason", "Override Reason"),
            ColumnSpec("approved_by", "Approved By"), ColumnSpec("occurred_on", "Occurred On"),
            ColumnSpec("allocation_dates", "Allocation Dates"),
        ],
    ),
    "rp08": (
        "Article Training Record", rpt.article_training_record,
        [
            ColumnSpec("article_name", "Article"), ColumnSpec("employee_code", "Code"),
            ColumnSpec("principal_name", "Principal"), ColumnSpec("clients_served", "Clients Served", "number"),
            ColumnSpec("service_types", "Service Types", "number"),
            ColumnSpec("exposure_diversity_score", "Exposure Score", "number"),
            ColumnSpec("leave_days_taken", "Leave Taken", "number"),
            ColumnSpec("leave_entitlement_days", "Leave Entitlement", "number"),
            ColumnSpec("form_103_filed", "Form 103"), ColumnSpec("form_108_filed", "Form 108"),
        ],
    ),
    "rp09": (
        "Leave and Absence", rpt.leave_and_absence,
        [
            ColumnSpec("staff_name", "Staff"), ColumnSpec("employee_code", "Code"), ColumnSpec("type", "Type"),
            ColumnSpec("date_from", "From"), ColumnSpec("date_to", "To"), ColumnSpec("days", "Days", "number"),
            ColumnSpec("status", "Status"), ColumnSpec("leave_entitlement_days", "Entitlement", "number"),
            ColumnSpec("conflicts_caused", "Conflicts Caused", "number"),
        ],
    ),
    "rp13": (
        "Independence and Rotation", rpt.independence_and_rotation_report,
        [
            ColumnSpec("client_name", "Client"), ColumnSpec("engagement_code", "Engagement"),
            ColumnSpec("ep_name", "Engagement Partner"), ColumnSpec("ep_tenure_years", "EP Tenure (yrs)", "number"),
            ColumnSpec("ep_rotation_due_fy", "EP Rotation Due FY"), ColumnSpec("firm_rotation_due_fy", "Firm Rotation Due FY"),
            ColumnSpec("eqcr_name", "EQCR"), ColumnSpec("open_conflicts", "Open Conflicts", "number"),
            ColumnSpec("declaration_status", "Declaration Status"), ColumnSpec("is_pie", "PIE"),
        ],
    ),
    "rp10": (
        "Engagement Profitability", rpt.engagement_profitability,
        [
            ColumnSpec("client_name", "Client"), ColumnSpec("engagement_code", "Engagement"),
            ColumnSpec("partner_name", "Partner"), ColumnSpec("fee_amount", "Fee", "money"),
            ColumnSpec("actual_cost", "Actual Cost", "money"), ColumnSpec("out_of_pocket_budget", "OOP Budget", "money"),
            ColumnSpec("margin_amount", "Margin", "money"), ColumnSpec("margin_pct", "Margin %", "pct"),
            ColumnSpec("budget_hours_total", "Budget Hrs", "number"), ColumnSpec("actual_hours", "Actual Hrs", "number"),
            ColumnSpec("hours_variance_pct", "Hrs Variance %", "number"), ColumnSpec("status", "Status"),
        ],
    ),
    "rp11": (
        "Timesheet Summary", rpt.timesheet_summary,
        [
            ColumnSpec("staff_name", "Staff"), ColumnSpec("employee_code", "Code"),
            ColumnSpec("engagement_code", "Engagement"), ColumnSpec("client_name", "Client"),
            ColumnSpec("hours_draft", "Draft Hrs", "number"), ColumnSpec("hours_submitted", "Submitted Hrs", "number"),
            ColumnSpec("hours_approved", "Approved Hrs", "number"), ColumnSpec("hours_rejected", "Rejected Hrs", "number"),
            ColumnSpec("chargeable_hours_approved", "Chargeable Approved Hrs", "number"),
        ],
    ),
    "rp12": (
        "Capacity Forecast", rpt.capacity_forecast,
        [
            ColumnSpec("month", "Month"), ColumnSpec("office_name", "Office"), ColumnSpec("department_name", "Department"),
            ColumnSpec("headcount", "Headcount", "number"), ColumnSpec("net_capacity_hrs", "Net Capacity Hrs", "number"),
            ColumnSpec("allocated_hrs", "Allocated Hrs", "number"),
            ColumnSpec("forecast_utilisation_pct", "Forecast Util %", "pct"),
        ],
    ),
    "rp14": (
        "Bench and Burnout Watchlist", rpt.bench_and_burnout_watchlist,
        [
            ColumnSpec("watchlist_type", "Type"), ColumnSpec("staff_name", "Staff"), ColumnSpec("employee_code", "Code"),
            ColumnSpec("designation", "Grade"), ColumnSpec("consecutive_count", "Consecutive", "number"),
            ColumnSpec("threshold", "Threshold", "number"), ColumnSpec("unit", "Unit"), ColumnSpec("as_of", "As Of"),
        ],
    ),
}


@router.get("")
def list_reports(user: User = Depends(get_current_user)) -> list[dict]:
    return [{"key": key, "title": title} for key, (title, _fn, _cols) in _REPORTS.items()]


@router.get("/{report_key}")
def get_report(
    report_key: str,
    date_from: date, date_to: date,
    office_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None, client_group_id: uuid.UUID | None = None,
    staff_category: str | None = None, status: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> list[dict]:
    if report_key not in _REPORTS:
        return []
    _title, fn, _cols = _REPORTS[report_key]
    f = _filters(office_id, department_id, partner_id, client_group_id, staff_category, status)
    return fn(db, date_from, date_to, f)


@router.get("/{report_key}/export.xlsx")
def export_report_xlsx(
    report_key: str,
    date_from: date, date_to: date,
    office_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None, client_group_id: uuid.UUID | None = None,
    staff_category: str | None = None, status: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> StreamingResponse:
    title, fn, cols = _REPORTS[report_key]
    f = _filters(office_id, department_id, partner_id, client_group_id, staff_category, status)
    rows = fn(db, date_from, date_to, f)
    xlsx_bytes = build_formatted_workbook(rows, cols, sheet_name=title)
    return StreamingResponse(
        iter([xlsx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={report_key}.xlsx"},
    )


@router.get("/{report_key}/export.pdf")
def export_report_pdf(
    report_key: str,
    date_from: date, date_to: date,
    office_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None, client_group_id: uuid.UUID | None = None,
    staff_category: str | None = None, status: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> StreamingResponse:
    title, fn, cols = _REPORTS[report_key]
    f = _filters(office_id, department_id, partner_id, client_group_id, staff_category, status)
    rows = fn(db, date_from, date_to, f)
    pdf_bytes = build_formatted_pdf(rows, cols, title=f"{title} ({date_from} to {date_to})")
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={report_key}.pdf"},
    )
