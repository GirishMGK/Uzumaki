"""EAD Files "Analytics" screen — pick which EAD-only checks to run against
the engagement's consolidated, ready EAD uploads, separate from the
existing per-upload "Run Analytics" (Customer Master KYC rules) flow.

Row-rule runs (exception flags) are persisted to disk under a run_id
(same convention as fcmr_core.reporting.builder's customer-master runs)
so their wide/long CSVs can be downloaded without recomputing. Summary
reports (product EAD reconciliation, System x Product min/max, month-wise
disbursal, NPA Flag Date changes) are cheap aggregations recomputed on
every request/download -- no persistence needed.

Row-rule runs execute in a FastAPI BackgroundTask rather than inline in
the POST handler: the Customer Master KYC flow (loan_app/api/runs.py) has
its own equivalent, DB-persisted (store.create_run/update_run) progress
tracking already, but its `runs` table requires a single upload_id (a
NOT NULL foreign key) -- EAD's "run" is against every ready upload
consolidated together, not one single upload, so reusing that table would
mean attributing the run to one arbitrary file. Progress here is instead
tracked in a small in-memory registry (_PROGRESS below), deliberately not
persisted: it only needs to answer "is my just-started run still going"
while a browser tab is actively polling it, not survive an app restart.
"""

from __future__ import annotations

import os
import tempfile
import threading
import urllib.parse
import uuid
from pathlib import Path

import polars as pl
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from fcmr_core.catalog import store
from fcmr_core.config import settings
from fcmr_core.reporting.aggregation import aggregate_exception_codes, aggregate_status_counts
from fcmr_core.reporting.builder import build_exception_csvs, build_exception_excel_by_rule
from fcmr_core.reporting.charts import build_bar_chart, build_donut_svg
from fcmr_core.rules.ead_brs_linking import (
    ead_vs_brs_collection_report,
    ead_vs_brs_disbursement_report,
    read_brs_export,
)
from fcmr_core.rules.ead_cross_dataset import (
    ucid_cross_check_report,
    written_off_customer_fresh_disbursal_report,
)
from fcmr_core.rules.ead_reports import (
    PIVOT_AGGREGATIONS,
    custom_pivot_report,
    month_wise_disbursal_summary,
    npa_flag_date_change_report,
    product_ead_reconciliation_summary,
    system_product_minmax_summary,
)
from fcmr_core.rules.ead_rules import list_ead_row_rules, run_ead_row_rules
from fcmr_core.schemas.loader import get_canonical_fields

router = APIRouter()
_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


def _smart_title(text: str) -> str:
    """Capitalize each word's first letter without lowercasing the rest,
    so mixed-case values like "ProdA" survive unlike str.title()."""
    return "".join(
        c.upper() if c.isalpha() and (i == 0 or not text[i - 1].isalpha()) else c for i, c in enumerate(text)
    )


templates.env.filters["smart_title"] = _smart_title


def _consolidated_ead_df(engagement_id: str | None) -> pl.DataFrame:
    return store.build_consolidated_df(engagement_id, "ead_files")


def _run_outputs_dir(run_id: str) -> Path:
    return settings.outputs_dir / f"ead_{run_id}"


def _brs_link_outputs_dir(run_id: str) -> Path:
    return settings.outputs_dir / f"ead_brs_{run_id}"


# run_id -> {"status": "running"|"completed"|"failed", "completed": int,
# "total": int, "rule_id": str, "error": str|None}. See module docstring
# for why this is a plain in-memory dict rather than the persisted `runs`
# table Customer Master's analytics use.
_PROGRESS: dict[str, dict] = {}
_PROGRESS_LOCK = threading.Lock()


def _set_progress(run_id: str, **fields) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS.setdefault(run_id, {}).update(fields)


def _get_progress(run_id: str) -> dict | None:
    with _PROGRESS_LOCK:
        entry = _PROGRESS.get(run_id)
        return dict(entry) if entry is not None else None


@router.get("/dashboard/analytics/ead", response_class=HTMLResponse)
async def ead_analytics_page(request: Request):
    engagement_id = request.session.get("engagement_id")
    uploads = store.list_uploads(engagement_id=engagement_id)
    ead_ready = [u for u in uploads if u["report_type"] == "ead_files" and u["status"] == "ready"]
    customer_master_ready = any(u["report_type"] == "customer_master" and u["status"] == "ready" for u in uploads)
    technical_writeoff_ready = any(
        u["report_type"] == "technical_writeoff" and u["status"] == "ready" for u in uploads
    )

    overrides, default_days = store.get_ead_sanction_disbursal_thresholds()
    product_types = sorted(set(store.get_system_type_map().values()))

    # product_helper isn't a schema-declared canonical field (ead_files.yaml
    # has no such column) -- it's tagged onto every consolidated EAD row at
    # ingest time from the System -> Product Type mapping (see uploads.py's
    # _tag_product_helper). It's also the single most-used grouping field
    # in the fixed reports above ("System x Product Name Min/Max", etc.),
    # so it's added here explicitly rather than left out just because it's
    # not in the YAML.
    pivot_canonicals = [c.canonical for c in get_canonical_fields("ead_files")] + ["product_helper"]
    pivot_fields = sorted(
        ({"canonical": c, "label": c.replace("_", " ").title()} for c in pivot_canonicals),
        key=lambda f: f["label"],
    )

    return templates.TemplateResponse(
        request=request,
        name="ead_analytics.html",
        context={
            "ready_count": len(ead_ready),
            "customer_master_ready": customer_master_ready,
            "technical_writeoff_ready": technical_writeoff_ready,
            "row_rules": list_ead_row_rules(),
            "default_days": default_days,
            "type_thresholds": [{"type": t, "days": overrides.get(t, default_days)} for t in product_types],
            "current_year": _current_fy_start_year(),
            "pivot_fields": pivot_fields,
            "pivot_aggs": list(PIVOT_AGGREGATIONS.keys()),
        },
    )


def _current_fy_start_year() -> int:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return now.year if now.month >= 4 else now.year - 1


def _execute_ead_run(
    run_id: str,
    df: pl.DataFrame,
    selected: list[str] | None,
    overrides: dict[str, int],
    default_days: int,
) -> None:
    def on_progress(completed: int, total: int, rule_id: str) -> None:
        _set_progress(run_id, completed=completed, total=total, rule_id=rule_id)

    try:
        annotated = run_ead_row_rules(
            df,
            selected,
            sanction_delay_thresholds=overrides,
            sanction_delay_default_days=default_days,
            on_progress=on_progress,
        )
        out_dir = _run_outputs_dir(run_id)
        _set_progress(run_id, step="Building CSV reports")
        build_exception_csvs(annotated, run_id, out_dir)
        # The Excel-by-rule export is the one step here whose cost scales
        # with how much a rule flagged, not just row count (see its own
        # docstring/_MAX_EXCEL_SHEET_ROWS) -- it's the only phase that can
        # meaningfully still be running once every rule itself is done, so
        # it gets its own distinct step label rather than looking frozen.
        _set_progress(run_id, step="Building Excel (by exception type)")
        build_exception_excel_by_rule(annotated, run_id, out_dir)
        _set_progress(run_id, status="completed", step="Done")
    except Exception as exc:
        _set_progress(run_id, status="failed", error=str(exc))


@router.post("/dashboard/analytics/ead/run")
async def ead_analytics_run(
    request: Request,
    background_tasks: BackgroundTasks,
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
    total_rules = len(selected) if selected is not None else len(list_ead_row_rules())

    store.set_ead_sanction_disbursal_threshold(None, default_days)
    overrides: dict[str, int] = {}
    for name, days_str in zip(type_names or [], type_days or [], strict=False):
        try:
            days = int(days_str)
        except ValueError:
            continue
        overrides[name] = days
        store.set_ead_sanction_disbursal_threshold(name, days)

    run_id = str(uuid.uuid4())
    _set_progress(run_id, status="running", completed=0, total=total_rules, rule_id="", step="", error=None)
    background_tasks.add_task(_execute_ead_run, run_id, df, selected, overrides, default_days)

    return RedirectResponse(url=f"/dashboard/analytics/ead/run/{run_id}", status_code=303)


@router.get("/dashboard/analytics/ead/run/{run_id}", response_class=HTMLResponse)
async def ead_analytics_run_detail(request: Request, run_id: str):
    progress = _get_progress(run_id)

    if progress and progress.get("status") == "running":
        return templates.TemplateResponse(
            request=request,
            name="ead_analytics_running.html",
            context={"run_id": run_id, "total": progress.get("total", 0), "error": None},
        )
    if progress and progress.get("status") == "failed":
        return templates.TemplateResponse(
            request=request,
            name="ead_analytics_running.html",
            context={"run_id": run_id, "total": 0, "error": progress.get("error") or "Unknown error"},
        )

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


@router.get("/dashboard/analytics/ead/run/{run_id}/progress")
async def ead_analytics_run_progress(run_id: str):
    progress = _get_progress(run_id)
    if not progress:
        raise HTTPException(status_code=404, detail="Unknown run")
    return JSONResponse(
        {
            "status": progress.get("status", "running"),
            "completed": progress.get("completed", 0),
            "total": progress.get("total", 0),
            "rule_id": progress.get("rule_id", ""),
            "step": progress.get("step", ""),
            "error": progress.get("error"),
        }
    )


@router.get("/dashboard/analytics/ead/run/{run_id}/download/{kind}")
async def ead_analytics_run_download(run_id: str, kind: str):
    if kind not in ("wide", "long", "excel"):
        raise HTTPException(status_code=404, detail="Unknown download kind")
    out_dir = _run_outputs_dir(run_id)
    if kind == "excel":
        path = out_dir / f"{run_id}_by_exception.xlsx"
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"EAD_Analytics_By_Exception_{run_id[:8]}.xlsx"
    else:
        path = out_dir / f"{run_id}_{kind}.csv"
        media_type = "text/csv"
        filename = f"EAD_Analytics_{kind}_{run_id[:8]}.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return FileResponse(path, media_type=media_type, filename=filename)


# ══════════════════════════════════════════════════════════════════════════════
# Summary reports (7, 9, 10, 18) and cross-dataset checks (16, 17) — all
# cheap aggregations/joins, recomputed on every request/download rather
# than persisted, same as EAD Consolidation and SQL Analytics.
# ══════════════════════════════════════════════════════════════════════════════
_SUMMARY_TITLES = {
    "product-recon": "Product-wise EAD Reconciliation",
    "system-product-minmax": "System x Product Name Min/Max Summary",
    "month-wise-disbursal": "Month-wise Disbursal Summary",
    "npa-flag-changes": "Multi-Month NPA Flag Date Changes",
    "ucid-cross-check": "UCID Cross-Check vs Customer Master",
    "written-off-fresh-disbursal": "Fresh Disbursal to Written-Off Customer",
}

# key -> (report_type of the second dataset needed, human label for the error)
_CROSS_DATASET_KEYS = {
    "ucid-cross-check": ("customer_master", "Customer Master"),
    "written-off-fresh-disbursal": ("technical_writeoff", "Technical Writeoff"),
}


def _build_summary_df(
    key: str, engagement_id: str | None, ead_df: pl.DataFrame, fy_start_year: int, include_state: bool
) -> pl.DataFrame:
    if key == "product-recon":
        return product_ead_reconciliation_summary(ead_df)
    if key == "system-product-minmax":
        return system_product_minmax_summary(ead_df)
    if key == "month-wise-disbursal":
        return month_wise_disbursal_summary(ead_df, fy_start_year, include_state)
    if key == "npa-flag-changes":
        return npa_flag_date_change_report(ead_df)
    if key in _CROSS_DATASET_KEYS:
        aux_report_type, aux_label = _CROSS_DATASET_KEYS[key]
        aux_df = store.build_consolidated_df(engagement_id, aux_report_type)
        if aux_df.is_empty():
            raise HTTPException(status_code=400, detail=f"No ready {aux_label} files found for this engagement.")
        if key == "ucid-cross-check":
            return ucid_cross_check_report(ead_df, aux_df)
        return written_off_customer_fresh_disbursal_report(ead_df, aux_df)
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
    result = _build_summary_df(key, engagement_id, df, fy_start_year, include_state)

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
    result = _build_summary_df(key, engagement_id, df, fy_start_year, include_state)

    # Written straight to a temp file and served via FileResponse (streamed
    # by Starlette) instead of building the CSV as a Python string, then
    # again as bytes, then handing that one in-memory blob to Response() as
    # the entire body -- same fix as the SQL Analytics export, applied here
    # for consistency.
    filename = f"EAD_{key.replace('-', '_')}.csv"
    fd, tmp_name = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    tmp_path = Path(tmp_name)
    result.write_csv(tmp_path)

    return FileResponse(
        tmp_path,
        media_type="text/csv",
        filename=filename,
        background=BackgroundTask(tmp_path.unlink, missing_ok=True),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Pivot — same "cheap, recomputed on every request" shape as the fixed
# summary reports above, except the row/column/value fields are picked by
# whoever's running it (any of the ~100 canonical EAD fields) instead of
# being hardcoded. The chosen config round-trips through the download
# URL's query string so the CSV download recomputes the same pivot rather
# than needing its own persisted run_id.
# ══════════════════════════════════════════════════════════════════════════════


def _pivot_title(rows: list[str], columns: list[str], value_field: str, agg: str) -> str:
    rows_label = " + ".join(r.replace("_", " ").title() for r in rows)
    agg_label = agg.replace("_", " ").title()
    title = f"Pivot: {rows_label}"
    if columns:
        title += " x " + " + ".join(c.replace("_", " ").title() for c in columns)
    return f"{title} — {agg_label} of {value_field.replace('_', ' ').title()}"


@router.post("/dashboard/analytics/ead/pivot", response_class=HTMLResponse)
async def ead_pivot_run(
    request: Request,
    rows: list[str] = Form(...),
    columns: list[str] = Form(default=[]),
    value_field: str = Form(...),
    agg: str = Form(...),
):
    engagement_id = request.session.get("engagement_id")
    df = _consolidated_ead_df(engagement_id)
    if df.is_empty():
        raise HTTPException(status_code=400, detail="No ready EAD files found for this engagement.")

    clean_rows = [r for r in rows if r]
    if not clean_rows:
        raise HTTPException(status_code=400, detail="Pick at least one Row field.")
    clean_columns = [c for c in columns if c]

    try:
        result = custom_pivot_report(df, clean_rows, clean_columns, value_field, agg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    params = urllib.parse.urlencode(
        [("rows", r) for r in clean_rows]
        + [("columns", c) for c in clean_columns]
        + [("value_field", value_field), ("agg", agg)]
    )

    return templates.TemplateResponse(
        request=request,
        name="ead_summary_result.html",
        context={
            "title": _pivot_title(clean_rows, clean_columns, value_field, agg),
            "columns": result.columns,
            "rows": result.to_dicts(),
            "row_count": len(result),
            "download_url": f"/dashboard/analytics/ead/pivot/download?{params}",
        },
    )


@router.get("/dashboard/analytics/ead/pivot/download")
async def ead_pivot_download(
    request: Request,
    rows: list[str] = Query(...),
    columns: list[str] = Query(default=[]),
    value_field: str = Query(...),
    agg: str = Query(...),
):
    engagement_id = request.session.get("engagement_id")
    df = _consolidated_ead_df(engagement_id)
    if df.is_empty():
        raise HTTPException(status_code=400, detail="No ready EAD files found for this engagement.")

    clean_rows = [r for r in rows if r]
    if not clean_rows:
        raise HTTPException(status_code=400, detail="Pick at least one Row field.")
    clean_columns = [c for c in columns if c]

    try:
        result = custom_pivot_report(df, clean_rows, clean_columns, value_field, agg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fd, tmp_name = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    tmp_path = Path(tmp_name)
    result.write_csv(tmp_path)

    return FileResponse(
        tmp_path,
        media_type="text/csv",
        filename="EAD_Pivot.csv",
        background=BackgroundTask(tmp_path.unlink, missing_ok=True),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Link to BRS — reconciles EAD against a one-off uploaded BRS Consolidator
# export (Disbursement or Collection). Unlike the summary reports above,
# the second dataset here is never catalogued (BRS Consolidator is a
# stateless standalone tool), so it can't be recomputed on a GET download
# request -- the result is persisted to disk under a run_id instead, same
# convention as the row-rule exception runs.
# ══════════════════════════════════════════════════════════════════════════════
_BRS_LINK_TITLES = {
    "disbursement": "EAD vs BRS Disbursement Reconciliation",
    "collection": "EAD vs BRS Collection Reconciliation",
}


@router.post("/dashboard/analytics/ead/brs-link/{kind}", response_class=HTMLResponse)
async def ead_brs_link_run(request: Request, kind: str, brs_file: UploadFile = File(...)):
    if kind not in _BRS_LINK_TITLES:
        raise HTTPException(status_code=404, detail="Unknown BRS link check")

    engagement_id = request.session.get("engagement_id")
    ead_df = _consolidated_ead_df(engagement_id)
    if ead_df.is_empty():
        raise HTTPException(status_code=400, detail="No ready EAD files found for this engagement.")

    data = await brs_file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded BRS file is empty.")

    try:
        brs_df = read_brs_export(brs_file.filename or "", data)
        if kind == "disbursement":
            result = ead_vs_brs_disbursement_report(ead_df, brs_df)
        else:
            result = ead_vs_brs_collection_report(ead_df, brs_df)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run_id = str(uuid.uuid4())
    out_dir = _brs_link_outputs_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    result.write_csv(out_dir / f"{run_id}.csv")

    return templates.TemplateResponse(
        request=request,
        name="ead_summary_result.html",
        context={
            "title": _BRS_LINK_TITLES[kind],
            "columns": result.columns,
            "rows": result.to_dicts(),
            "row_count": len(result),
            "download_url": f"/dashboard/analytics/ead/brs-link/{run_id}/download",
        },
    )


@router.get("/dashboard/analytics/ead/brs-link/{run_id}/download")
async def ead_brs_link_download(run_id: str):
    path = _brs_link_outputs_dir(run_id) / f"{run_id}.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return FileResponse(path, media_type="text/csv", filename=f"EAD_BRS_Link_{run_id[:8]}.csv")
