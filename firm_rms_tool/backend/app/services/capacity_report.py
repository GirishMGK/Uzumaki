"""Read side of capacity_daily — aggregate queries for reports/dashboards.

§5: "Reports must never recompute from raw allocations at query time for
ranges > 90 days." Every function here reads only capacity_daily, via a
single GROUP BY query regardless of staff count or range length — that's
what makes the P5 DoD number (300 staff x 90 days in < 2s) possible.
"""
from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from sqlmodel import Session, func, select

from app.models.capacity import CapacityDaily
from app.models.staff import Staff


def get_staff_utilisation(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    office_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    staff_ids: list[uuid.UUID] | None = None,
) -> list[dict]:
    stmt = (
        select(
            CapacityDaily.staff_id,
            Staff.full_name,
            Staff.designation,
            Staff.chargeability_target_pct,
            func.sum(CapacityDaily.net_capacity_hrs).label("net_capacity_hrs"),
            func.sum(CapacityDaily.allocated_hrs).label("allocated_hrs"),
            func.sum(CapacityDaily.soft_allocated_hrs).label("soft_allocated_hrs"),
            func.sum(CapacityDaily.chargeable_hrs).label("chargeable_hrs"),
            func.sum(CapacityDaily.available_hrs).label("available_hrs"),
            func.sum(sa.cast(CapacityDaily.bench_flag, sa.Integer)).label("bench_days"),
        )
        .join(Staff, Staff.id == CapacityDaily.staff_id)
        .where(CapacityDaily.capacity_date >= date_from)
        .where(CapacityDaily.capacity_date <= date_to)
        .where(Staff.is_active == True)  # noqa: E712
        .group_by(CapacityDaily.staff_id, Staff.full_name, Staff.designation, Staff.chargeability_target_pct)
    )
    if office_id:
        stmt = stmt.where(Staff.base_office_id == office_id)
    if department_id:
        stmt = stmt.where(Staff.primary_department_id == department_id)
    if staff_ids:
        stmt = stmt.where(CapacityDaily.staff_id.in_(staff_ids))  # type: ignore[attr-defined]

    out = []
    for r in db.exec(stmt).all():
        net = r.net_capacity_hrs or 0
        out.append(
            {
                "staff_id": r.staff_id,
                "full_name": r.full_name,
                "designation": r.designation,
                "net_capacity_hrs": round(net, 2),
                "allocated_hrs": round(r.allocated_hrs or 0, 2),
                "soft_allocated_hrs": round(r.soft_allocated_hrs or 0, 2),
                "chargeable_hrs": round(r.chargeable_hrs or 0, 2),
                "available_hrs": round(r.available_hrs or 0, 2),
                "utilisation_pct": round((r.allocated_hrs or 0) / net * 100, 2) if net else 0.0,
                "chargeable_util_pct": round((r.chargeable_hrs or 0) / net * 100, 2) if net else 0.0,
                "target_chargeability_pct": r.chargeability_target_pct,
                "bench_days": int(r.bench_days or 0),
            }
        )
    return out
