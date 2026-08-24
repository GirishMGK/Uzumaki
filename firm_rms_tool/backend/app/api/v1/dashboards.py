"""C1-C6 mandatory dashboards (§7.1) — data, drill-through, and Excel export.

Every endpoint accepts the same shared filter set (§7: "a shared filter
bar ... applying to every chart") — office, department, partner, client
group, staff category, plus a date range for the FTE-based charts
(C3-C6). C1/C2 are headcount snapshots and don't take a date range.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.core.deps import get_current_user, get_db
from app.models.staff import Staff
from app.models.user import User
from app.reports.excel_export import ColumnSpec, build_formatted_workbook
from app.services import dashboard as dash

router = APIRouter()


def _filters(
    office_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None,
    client_group_id: uuid.UUID | None = None,
    staff_category: str | None = None,
) -> dash.DashboardFilters:
    return dash.DashboardFilters(
        office_id=office_id, department_id=department_id, partner_id=partner_id,
        client_group_id=client_group_id, staff_category=staff_category,
    )


@router.get("/c1-headcount")
def c1_headcount(office_id: uuid.UUID | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    return dash.headcount_by_office_category(db, office_id=office_id)


@router.get("/c2-location-grade")
def c2_location_grade(office_id: uuid.UUID | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    return dash.headcount_by_office_grade(db, office_id=office_id)


@router.get("/c3-partner-fte")
def c3_partner_fte(
    date_from: date, date_to: date,
    office_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None, client_group_id: uuid.UUID | None = None, staff_category: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> list[dict]:
    f = _filters(office_id, department_id, partner_id, client_group_id, staff_category)
    return dash.partner_wise_fte(db, date_from, date_to, f)


@router.get("/c4-partner-portfolio")
def c4_partner_portfolio(
    date_from: date, date_to: date,
    office_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None, client_group_id: uuid.UUID | None = None, staff_category: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> list[dict]:
    f = _filters(office_id, department_id, partner_id, client_group_id, staff_category)
    return dash.partner_portfolio(db, date_from, date_to, f)


@router.get("/c5-department-fte")
def c5_department_fte(
    date_from: date, date_to: date,
    office_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None, client_group_id: uuid.UUID | None = None, staff_category: str | None = None,
    include_trend: bool = True,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    f = _filters(office_id, department_id, partner_id, client_group_id, staff_category)
    current = dash.department_wise_fte(db, date_from, date_to, f)
    trend = dash.department_wise_fte_trend(db, filters=f) if include_trend else []
    return {"current": current, "trend": trend}


@router.get("/c6-department-grade")
def c6_department_grade(
    date_from: date, date_to: date,
    office_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None, client_group_id: uuid.UUID | None = None, staff_category: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> list[dict]:
    f = _filters(office_id, department_id, partner_id, client_group_id, staff_category)
    return dash.department_by_grade(db, date_from, date_to, f)


@router.get("/drill")
def drill_through(
    date_from: date, date_to: date,
    office_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None, client_group_id: uuid.UUID | None = None, staff_category: str | None = None,
    designation: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> list[dict]:
    """Underlying allocation records for a clicked chart segment (§7: "every
    chart supports drill-through to the underlying record list")."""
    f = _filters(office_id, department_id, partner_id, client_group_id, staff_category)
    rows = dash.fetch_allocation_fte_rows(db, date_from, date_to, f)
    if designation:
        rows = [r for r in rows if r.designation == designation]
    return [
        {
            "allocation_id": r.allocation_id, "staff_id": r.staff_id, "staff_name": r.staff_name,
            "designation": r.designation, "department_id": r.department_id, "partner_id": r.partner_id,
            "engagement_id": r.engagement_id, "engagement_code": r.engagement_code,
            "client_id": r.client_id, "client_name": r.client_name, "role_on_engagement": r.role_on_engagement,
            "date_from": r.date_from, "date_to": r.date_to, "allocation_pct": r.allocation_pct, "fte": r.fte,
        }
        for r in rows
    ]


@router.get("/drill-headcount")
def drill_headcount(
    office_id: uuid.UUID | None = None,
    staff_category: str | None = None,
    designation: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> list[dict]:
    """Drill-through for C1/C2 (headcount snapshots — no allocation involved)."""
    stmt = select(Staff).where(Staff.is_active == True)  # noqa: E712
    if office_id:
        stmt = stmt.where(Staff.base_office_id == office_id)
    if staff_category:
        stmt = stmt.where(Staff.staff_category == staff_category)
    if designation:
        stmt = stmt.where(Staff.designation == designation)
    return [
        {
            "staff_id": s.id, "full_name": s.full_name, "employee_code": s.employee_code,
            "staff_category": s.staff_category, "designation": s.designation,
        }
        for s in db.exec(stmt).all()
    ]


_EXPORT_COLUMNS: dict[str, list[ColumnSpec]] = {
    "c1": [ColumnSpec("office_name", "Office"), ColumnSpec("staff_category", "Category"), ColumnSpec("count", "Headcount", "number")],
    "c2": [ColumnSpec("office_name", "Office"), ColumnSpec("designation", "Designation"), ColumnSpec("count", "Headcount", "number")],
    "c3": [ColumnSpec("partner_name", "Partner"), ColumnSpec("designation", "Grade"), ColumnSpec("fte", "FTE", "number")],
    "c4": [
        ColumnSpec("partner_name", "Partner"), ColumnSpec("fee_under_management", "Fee under management", "money"),
        ColumnSpec("fte_deployed", "FTE deployed", "number"), ColumnSpec("engagement_count", "Engagements", "number"),
        ColumnSpec("avg_risk_score", "Avg risk score", "number"),
    ],
    "c5": [ColumnSpec("department_name", "Department"), ColumnSpec("fte", "FTE", "number")],
    "c6": [ColumnSpec("department_name", "Department"), ColumnSpec("designation", "Grade"), ColumnSpec("fte", "FTE", "number")],
}

_CHART_FUNCS = {
    "c1": lambda db, date_from, date_to, f: dash.headcount_by_office_category(db, office_id=f.office_id),
    "c2": lambda db, date_from, date_to, f: dash.headcount_by_office_grade(db, office_id=f.office_id),
    "c3": lambda db, date_from, date_to, f: dash.partner_wise_fte(db, date_from, date_to, f),
    "c4": lambda db, date_from, date_to, f: dash.partner_portfolio(db, date_from, date_to, f),
    "c5": lambda db, date_from, date_to, f: dash.department_wise_fte(db, date_from, date_to, f),
    "c6": lambda db, date_from, date_to, f: dash.department_by_grade(db, date_from, date_to, f),
}


@router.get("/{chart}/export")
def export_chart(
    chart: str,
    date_from: date | None = None, date_to: date | None = None,
    office_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None,
    partner_id: uuid.UUID | None = None, client_group_id: uuid.UUID | None = None, staff_category: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> StreamingResponse:
    if chart not in _CHART_FUNCS:
        return StreamingResponse(iter([b""]), status_code=404)
    f = _filters(office_id, department_id, partner_id, client_group_id, staff_category)
    d_from = date_from or date.today().replace(day=1)
    d_to = date_to or date.today()
    rows = _CHART_FUNCS[chart](db, d_from, d_to, f)
    xlsx_bytes = build_formatted_workbook(rows, _EXPORT_COLUMNS[chart], sheet_name=chart.upper())
    return StreamingResponse(
        iter([xlsx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={chart}_export.xlsx"},
    )
