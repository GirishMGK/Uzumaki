"""EAD Files "Analytics" screen — pick which EAD-only checks to run against
the engagement's consolidated, ready EAD uploads, separate from the
existing per-upload "Run Analytics" (Customer Master KYC rules) flow.

Row-rule runs (exception flags) are persisted to disk under a run_id
(same convention as fcmr_core.reporting.builder's customer-master runs)
so their wide/long CSVs can be downloaded without recomputing. Summary
reports (product EAD reconciliation, System x Product min/max, month-wise
disbursal, NPA Flag Date changes) are cheap aggregations recomputed on
every request/download -- no persistence needed.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import polars as pl
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from fcmr_core.catalog import store
from fcmr_core.config import settings
from fcmr_core.reporting.aggregation import aggregate_exception_codes, aggregate_status_counts
from fcmr_core.reporting.builder import build_exception_csvs
from fcmr_core.reporting.charts import build_bar_chart, build_donut_svg
from fcmr_core.rules.ead_reports import (
    month_wise_disbursal_summary,
    npa_flag_date_change_report,
    product_ead_reconciliation_summary,
    system_product_minmax_summary,
)
from fcmr_core.rules.ead_rules import list_ead_row_rules, run_ead_row_rules

router = APIRouter()
_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


def _consolidated_ead_df(engagement_id: str | None) -> pl.DataFrame:
    return store.build_consolidated_df(engagement_id, "ead_files")


def _run_outputs_dir(run_id: str) -> Path:
    return settings.outputs_dir / f"ead_{run_id}"


@router.get("/dashboard/analytics/ead", response_class=HTMLResponse)
async def ead_analytics_page(request: Request):
    engagement_id = request.session.get("engagement_id")
    uploads = store.list_uploads(engagement_id=engagement_id)
    ead_ready = [u for u in uploads if u["report_type"] == "ead_files" and u["status"] == "ready"]

    overrides, default_days = store.get_ead_sanction_disbursal_thresholds()
    product_types = sorted(set(store.get_system_type_map().values()))

    return templates.TemplateResponse(
        request=request,
        name="ead_analytics.html",
        context={
            "ready_count": len(ead_ready),
            "row_rules": list_ead_row_rules(),
            "default_days": default_days,
            "type_thresholds": [{"type": t, "days": overrides.get(t, default_days)} for t in product_types],
            "current_year": _current_fy_start_year(),
        },
    )


def _current_fy_start_year() -> int:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return now.year if now.month >= 4 else now.year - 1


@router.post("/dashboard/analytics/ead/run", response_class=HTMLResponse)
async def ead_analytics_run(
    request: Request,
    rules: list[str] | None = Form(None),
    default_days: int = Form(30),
    type_names: list[str] | None = Form(None),
    type_days: list[str] | None = Form(None),
):
    engagement_id = request.session.get("engagement_id")
    df = _consolidated_ead_df(engagement_id)
    if df.is_empty():
        raise HTTPException(status_code=400, detail="No ready EAD files found for this engagement.")

    valid_ids = {m.rule_id for m in list_ead_row_rules()}
    selected = [r for r in (rules or []) if r in valid_ids] or None

    store.set_ead_sanction_disbursal_threshold(None, default_days)
    overrides: dict[str, int] = {}
    for name, days_str in zip(type_names or [], type_days or [], strict=False):
        try:
            days = int(days_str)
        except ValueError:
            continue
        overrides[name] = days
        store.set_ead_sanction_disbursal_threshold(name, days)

    annotated = run_ead_row_rules(
        df, selected, sanction_delay_thresholds=overrides, sanction_delay_default_days=default_days
    )

    run_id = str(uuid.uuid4())
    out_dir = _run_outputs_dir(run_id)
    build_exception_csvs(annotated, run_id, out_dir)

    return await ead_analytics_run_detail(request, run_id)


@router.get("/dashboard/analytics/ead/run/{run_id}", response_class=HTMLResponse)
async def ead_analytics_run_detail(request: Request, run_id: str):
    wide_path = _run_outputs_dir(run_id) / f"{run_id}_wide.csv"
    if not wide_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")

    status_counts = aggregate_status_counts(wide_path)
    all_codes = aggregate_exception_codes(wide_path, top_n=None)
    top_codes = aggregate_exception_codes(wide_path, top_n=10)
    total = sum(status_counts.values())

    donut_svg = build_donut_svg(status_counts, width=300, height=300)
    bar_svg = build_bar_chart(top_codes, width=700, height=400)

    summary = {
        "total": total,
        "status_counts": status_counts,
        "all_codes": [{"exception_code": c, "count": n} for c, n in all_codes.items()],
    }

    return templates.TemplateResponse(
        request=request,
        name="ead_analytics_result.html",
        context={"run_id": run_id, "summary": summary, "donut_svg": donut_svg, "bar_svg": bar_svg},
    )


@router.get("/dashboard/analytics/ead/run/{run_id}/download/{kind}")
async def ead_analytics_run_download(run_id: str, kind: str):
    if kind not in ("wide", "long"):
        raise HTTPException(status_code=404, detail="Unknown download kind")
    path = _run_outputs_dir(run_id) / f"{run_id}_{kind}.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return FileResponse(path, media_type="text/csv", filename=f"EAD_Analytics_{kind}_{run_id[:8]}.csv")


# ══════════════════════════════════════════════════════════════════════════════
# Summary reports (7, 9, 10, 18) — cheap aggregations, recomputed each time
# ══════════════════════════════════════════════════════════════════════════════
_SUMMARY_TITLES = {
    "product-recon": "Product-wise EAD Reconciliation",
    "system-product-minmax": "System x Product Name Min/Max Summary",
    "month-wise-disbursal": "Month-wise Disbursal Summary",
    "npa-flag-changes": "Multi-Month NPA Flag Date Changes",
}


def _build_summary_df(key: str, df: pl.DataFrame, fy_start_year: int, include_state: bool) -> pl.DataFrame:
    if key == "product-recon":
        return product_ead_reconciliation_summary(df)
    if key == "system-product-minmax":
        return system_product_minmax_summary(df)
    if key == "month-wise-disbursal":
        return month_wise_disbursal_summary(df, fy_start_year, include_state)
    if key == "npa-flag-changes":
        return npa_flag_date_change_report(df)
    raise HTTPException(status_code=404, detail="Unknown summary report")


@router.post("/dashboard/analytics/ead/summary/{key}", response_class=HTMLResponse)
async def ead_summary_run(
    request: Request,
    key: str,
    fy_start_year: int = Form(0),
    include_state: bool = Form(False),
):
    if key not in _SUMMARY_TITLES:
        raise HTTPException(status_code=404, detail="Unknown summary report")
    engagement_id = request.session.get("engagement_id")
    df = _consolidated_ead_df(engagement_id)
    if df.is_empty():
        raise HTTPException(status_code=400, detail="No ready EAD files found for this engagement.")

    fy_start_year = fy_start_year or _current_fy_start_year()
    result = _build_summary_df(key, df, fy_start_year, include_state)

    return templates.TemplateResponse(
        request=request,
        name="ead_summary_result.html",
        context={
            "title": _SUMMARY_TITLES[key],
            "columns": result.columns,
            "rows": result.to_dicts(),
            "row_count": len(result),
            "download_url": f"/dashboard/analytics/ead/summary/{key}/download?fy_start_year={fy_start_year}&include_state={include_state}",
        },
    )


@router.get("/dashboard/analytics/ead/summary/{key}/download")
async def ead_summary_download(
    request: Request,
    key: str,
    fy_start_year: int = 0,
    include_state: bool = False,
):
    if key not in _SUMMARY_TITLES:
        raise HTTPException(status_code=404, detail="Unknown summary report")
    engagement_id = request.session.get("engagement_id")
    df = _consolidated_ead_df(engagement_id)
    if df.is_empty():
        raise HTTPException(status_code=400, detail="No ready EAD files found for this engagement.")

    fy_start_year = fy_start_year or _current_fy_start_year()
    result = _build_summary_df(key, df, fy_start_year, include_state)

    csv_bytes = result.write_csv().encode("utf-8")
    filename = f"EAD_{key.replace('-', '_')}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
