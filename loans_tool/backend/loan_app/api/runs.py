"""Run (analytics execution) endpoints."""

from __future__ import annotations

import gc
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import psutil
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from fcmr_core.catalog import store
from fcmr_core.config import settings
from fcmr_core.ingestion.pipeline import read_parquet
from fcmr_core.logging_setup import get_logger
from fcmr_core.reporting.aggregation import aggregate_exception_codes, aggregate_status_counts
from fcmr_core.reporting.builder import build_exception_csvs
from fcmr_core.reporting.charts import build_bar_chart, build_donut_svg
from fcmr_core.reporting.workpaper import build_workpaper
from fcmr_core.rules.registry import resolve_rule_ids, run_pipeline
from fcmr_core.sampling.sample import select_sample

logger = get_logger("processing")

router = APIRouter()

# In-memory set of run IDs that have been requested to cancel.
_cancel_requests: set[str] = set()


class _RunCancelled(Exception):
    """Raised by the on_progress callback when a cancel is requested mid-pipeline."""


_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


@router.post("/uploads/{upload_id}/run")
async def start_run(
    upload_id: str,
    background_tasks: BackgroundTasks,
    mode: str = Form("all"),
    categories: list[str] | None = Form(None),
    rules: list[str] | None = Form(None),
):
    upload = store.get_upload(upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload["status"] != "ready":
        raise HTTPException(status_code=400, detail="Upload is not ready")

    run_id = store.create_run(upload_id)
    store.update_run(run_id, status="running", started_at=_now())

    # Resolve category selection to rule_ids
    if mode == "all":
        rule_ids = None
        logger.info("start_run mode=all upload_id=%s", upload_id)
    else:
        rule_ids = resolve_rule_ids(categories or [], rules or [])
        logger.info(
            "start_run mode=selected upload_id=%s categories=%s rules=%s resolved=%s",
            upload_id,
            categories,
            rules,
            rule_ids,
        )

    background_tasks.add_task(_run_analytics, run_id, upload_id, rule_ids)

    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] not in ("running", "pending"):
        raise HTTPException(status_code=400, detail="Run is not in progress")
    _cancel_requests.add(run_id)
    return JSONResponse({"status": "cancel_requested"})


@router.get("/runs/{run_id}/status")
async def run_status(run_id: str):
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return JSONResponse(
        {
            "status": run["status"],
            "progress_step": run.get("progress_step") or "",
            "progress_pct": run.get("progress_pct") or 0,
            "error": run.get("error") or "",
        }
    )


def _run_analytics(run_id: str, upload_id: str, rule_ids: list[str] | None = None) -> None:
    """Execute the full analytics pipeline and update the catalog when done."""
    import json

    def _check_cancel() -> None:
        """Raise _RunCancelled if a stop was requested."""
        if run_id in _cancel_requests:
            raise _RunCancelled()

    def _on_rule_progress(completed: int, total: int, rule_id: str) -> None:
        """Called by run_pipeline after each rule. Updates DB with real % progress."""
        _check_cancel()
        # Rules span 5% → 90% of the total progress range.
        pct = 5 + int((completed / total) * 85)
        label = f"Rule {completed}/{total}: {rule_id.replace('_', ' ')}"
        store.update_run(run_id, progress_step=label, progress_pct=pct)

    try:
        upload = store.get_upload(upload_id)
        logger.info("job_start run_id=%s upload_id=%s rule_ids=%s", run_id, upload_id, rule_ids)

        # Store the selected rules (None = all)
        selected_rules_str = json.dumps(rule_ids) if rule_ids else None
        store.update_run(run_id, selected_rules=selected_rules_str)

        _check_cancel()
        store.update_run(run_id, progress_step="Loading data file", progress_pct=2)

        # Pre-flight: warn if available RAM is below 2 GB before we start
        available_gb = psutil.virtual_memory().available / 1024**3
        if available_gb < 2.0:
            logger.warning(
                "low_ram run_id=%s available_gb=%.1f — processing will rely on DuckDB disk spill",
                run_id,
                available_gb,
            )

        # Data lives in DuckDB after ingestion; parquet is deleted post-import.
        try:
            df = store.get_upload_df(upload_id)
        except Exception:
            parquet_path = Path(upload["parquet_path"])
            if not parquet_path.exists():
                raise FileNotFoundError(
                    f"Data file not found at {parquet_path}. " "Please re-upload the CSV file."
                )
            df = read_parquet(parquet_path).collect()

        # Cast every column to string up-front. DuckDB infers numeric-looking
        # fields (mobile, pincode, bank_account) as Int64; all rules expect str.
        df = df.with_columns([pl.col(c).cast(pl.Utf8, strict=False) for c in df.columns])
        logger.info("job_loaded run_id=%s rows=%d", run_id, len(df))

        store.update_run(run_id, progress_step="Starting validation rules…", progress_pct=5)
        annotated = run_pipeline(df, on_progress=_on_rule_progress, rule_ids=rule_ids)

        # Release the input frame and rule-annotated columns we no longer need.
        del df
        gc.collect()

        store.update_run(run_id, progress_step="Building exception reports", progress_pct=90)
        out_dir = settings.outputs_dir / run_id
        wide_path, long_path = build_exception_csvs(annotated, run_id, out_dir)

        del annotated
        gc.collect()

        logger.info(
            "job_complete run_id=%s wide=%s long=%s", run_id, wide_path.name, long_path.name
        )
        store.update_run(
            run_id,
            status="completed",
            finished_at=_now(),
            wide_csv=str(wide_path),
            long_csv=str(long_path),
            progress_step="Done",
            progress_pct=100,
        )
    except _RunCancelled:
        _cancel_requests.discard(run_id)
        store.update_run(run_id, status="cancelled", finished_at=_now(), error="Stopped by user.")
        logger.info("job_cancelled run_id=%s", run_id)
    except Exception as exc:
        logger.error("job_failed run_id=%s error=%s", run_id, type(exc).__name__)
        store.update_run(run_id, status="failed", finished_at=_now(), error=str(exc))


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(request: Request, run_id: str):
    import json

    from fcmr_core.rules.registry import CATEGORIES

    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    donut_svg = None
    bar_svg = None
    summary = None
    ran_categories = None

    # Map selected_rules to category labels
    if run.get("selected_rules"):
        try:
            selected_rules = json.loads(run["selected_rules"])
            if selected_rules:
                selected_set = set(selected_rules)
                matching_cats = []
                for cat in CATEGORIES:
                    if all(r in selected_set for r in cat["rule_ids"]):
                        matching_cats.append(cat["label"])
                if matching_cats:
                    ran_categories = ", ".join(matching_cats)
        except Exception:
            pass
    if not ran_categories:
        ran_categories = "All Categories"

    if run["status"] == "completed" and run["wide_csv"]:
        wide_path = Path(run["wide_csv"])
        if wide_path.exists():
            status_counts = aggregate_status_counts(wide_path)
            # Get ALL exception codes (not just top 10)
            all_exception_codes = aggregate_exception_codes(wide_path, top_n=None)
            # Get top 10 for the bar chart (for readability)
            top_exception_codes = aggregate_exception_codes(wide_path, top_n=10)

            donut_svg = build_donut_svg(status_counts, width=300, height=300)
            bar_svg = build_bar_chart(top_exception_codes, width=700, height=400)

            total = sum(status_counts.values())
            all_codes = [{"exception_code": c, "count": n} for c, n in all_exception_codes.items()]
            summary = {
                "total": total,
                "status_counts": status_counts,
                "exception_codes": top_exception_codes,
                "all_codes": all_codes,
            }

    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context={
            "run": run,
            "summary": summary,
            "donut_svg": donut_svg,
            "bar_svg": bar_svg,
            "ran_categories": ran_categories,
        },
    )


@router.get("/runs/{run_id}/export/svg")
async def export_charts_svg(run_id: str):
    """Export donut + bar charts as a single SVG."""
    run = store.get_run(run_id)
    if not run or run["status"] != "completed" or not run["wide_csv"]:
        raise HTTPException(status_code=404, detail="Run not found or not completed")

    wide_path = Path(run["wide_csv"])
    if not wide_path.exists():
        raise HTTPException(status_code=404, detail="Wide CSV not found")

    status_counts = aggregate_status_counts(wide_path)
    exception_codes = aggregate_exception_codes(wide_path, top_n=10)

    donut_svg = build_donut_svg(status_counts, width=300, height=300)
    bar_svg = build_bar_chart(exception_codes, width=700, height=400)

    combined_svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg viewBox="0 0 1000 900" xmlns="http://www.w3.org/2000/svg">
  <style>
    .title {{ font-size: 18px; font-weight: bold; fill: #1f2937; }}
  </style>
  <text x="20" y="30" class="title">Analytics Summary</text>

  <!-- Donut chart -->
  <g transform="translate(20, 60)">
    {donut_svg}
  </g>

  <!-- Bar chart -->
  <g transform="translate(20, 450)">
    {bar_svg}
  </g>
</svg>"""

    return combined_svg


@router.get("/runs/{run_id}/charts/donut.svg")
async def get_donut_chart(run_id: str):
    """Get donut chart SVG for embedding."""
    run = store.get_run(run_id)
    if not run or run["status"] != "completed" or not run["wide_csv"]:
        raise HTTPException(status_code=404, detail="Run not found or not completed")

    wide_path = Path(run["wide_csv"])
    if not wide_path.exists():
        raise HTTPException(status_code=404, detail="Wide CSV not found")

    status_counts = aggregate_status_counts(wide_path)
    donut_svg = build_donut_svg(status_counts, width=300, height=300)

    return {"donut_svg": donut_svg}


@router.get("/runs/{run_id}/charts/bar.svg")
async def get_bar_chart(run_id: str):
    """Get bar chart SVG for embedding."""
    run = store.get_run(run_id)
    if not run or run["status"] != "completed" or not run["wide_csv"]:
        raise HTTPException(status_code=404, detail="Run not found or not completed")

    wide_path = Path(run["wide_csv"])
    if not wide_path.exists():
        raise HTTPException(status_code=404, detail="Wide CSV not found")

    exception_codes = aggregate_exception_codes(wide_path, top_n=10)
    bar_svg = build_bar_chart(exception_codes, width=700, height=400)

    return {"bar_svg": bar_svg}


@router.get("/runs/{run_id}/export/workpaper")
async def export_workpaper(run_id: str):
    """Generate and download workpaper Excel."""
    run = store.get_run(run_id)
    if not run or run["status"] != "completed" or not run["wide_csv"] or not run["long_csv"]:
        raise HTTPException(status_code=404, detail="Run not found or not completed")

    # Lookup engagement; use "default" as fallback if missing or not found
    engagement_id = run.get("engagement_id") or "default"
    engagement = store.get_engagement(engagement_id)
    if not engagement:
        # Create a minimal engagement object if not found
        engagement = {
            "engagement_id": engagement_id,
            "name": "Audit Engagement",
            "client_name": "—",
            "period_from": None,
            "period_to": None,
        }

    wide_path = Path(run["wide_csv"])
    long_path = Path(run["long_csv"])
    if not wide_path.exists() or not long_path.exists():
        raise HTTPException(status_code=404, detail="CSV files not found")

    try:
        df = pl.read_csv(wide_path, infer_schema_length=0)
        population = len(df)
        exception_count = sum(1 for val in df["overall_status"] if val != "OK")

        sample_records = select_sample(
            wide_path,
            engagement_id=run["engagement_id"],
            run_id=run_id,
            population=population,
            exception_count=exception_count,
        )

        workpaper_path = build_workpaper(
            engagement=engagement,
            run=run,
            wide_csv_path=wide_path,
            sample_records=sample_records,
            output_dir=settings.outputs_dir / run_id,
        )

        store.update_run(run_id, workpaper_path=str(workpaper_path))

        return FileResponse(
            workpaper_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=workpaper_path.name,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Workpaper generation failed: {exc}") from exc


def _now() -> str:
    return datetime.now(UTC).isoformat()
