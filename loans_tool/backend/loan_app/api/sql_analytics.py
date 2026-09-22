"""SQL Analytics — ad-hoc DuckDB SQL over the engagement's ingested reports.

An escape hatch for when the built-in rule checks (KYC format, duplicates,
UCID, PIN/address, EAD/ECL consolidation) don't cover what someone needs.
Each report type with a canonical schema (customer_master, ead_files,
technical_writeoff, collection_report) is exposed as one table -- the
consolidation of every ready upload of that type for the current
engagement, via the same store.build_consolidated_df() EAD Consolidation
uses. No re-upload needed: whatever's already been ingested and mapped is
immediately queryable.

Runs entirely against a fresh in-memory DuckDB connection with just those
DataFrames registered -- never against the app's own catalog.duckdb (which
also holds user accounts and password hashes). That connection is created
fresh per request and discarded after, so there's nothing for a query to
persist or corrupt even if it tries a DDL/DML statement.
"""

from __future__ import annotations

import io
from pathlib import Path

import duckdb
import polars as pl
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from fcmr_core.catalog import store
from fcmr_core.schemas.loader import available_report_types

router = APIRouter()
_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

PREVIEW_ROW_LIMIT = 500


def _load_tables(engagement_id: str | None) -> dict[str, pl.DataFrame]:
    tables: dict[str, pl.DataFrame] = {}
    for report_type in available_report_types():
        df = store.build_consolidated_df(engagement_id, report_type)
        if not df.is_empty():
            tables[report_type] = df
    return tables


def _run_query(tables: dict[str, pl.DataFrame], sql: str) -> pl.DataFrame:
    con = duckdb.connect(":memory:")
    try:
        for name, df in tables.items():
            con.register(name, df)
        return con.execute(sql).pl()
    finally:
        con.close()


@router.get("/dashboard/analytics/sql", response_class=HTMLResponse)
async def sql_analytics_page(request: Request):
    engagement_id = request.session.get("engagement_id")
    tables = _load_tables(engagement_id)
    table_info = [
        {"name": name, "rows": len(df), "columns": df.columns} for name, df in tables.items()
    ]
    default_sql = f"SELECT * FROM {table_info[0]['name']} LIMIT 100" if table_info else ""
    return templates.TemplateResponse(
        request=request,
        name="sql_analytics.html",
        context={"tables": table_info, "default_sql": default_sql},
    )


@router.post("/dashboard/analytics/sql/run")
async def sql_analytics_run(request: Request):
    engagement_id = request.session.get("engagement_id")
    form = await request.form()
    sql = str(form.get("sql", "")).strip()
    if not sql:
        return JSONResponse({"error": "Enter a SQL query."}, status_code=400)

    tables = _load_tables(engagement_id)
    if not tables:
        return JSONResponse(
            {"error": "No ingested files ready yet — upload and map at least one file first."},
            status_code=400,
        )

    try:
        result = _run_query(tables, sql)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse(
        {
            "columns": result.columns,
            "rows": result.head(PREVIEW_ROW_LIMIT).to_dicts(),
            "total_rows": len(result),
            "preview_limit": PREVIEW_ROW_LIMIT,
        }
    )


@router.post("/dashboard/analytics/sql/export")
async def sql_analytics_export(request: Request):
    engagement_id = request.session.get("engagement_id")
    form = await request.form()
    sql = str(form.get("sql", "")).strip()
    if not sql:
        raise HTTPException(status_code=400, detail="Enter a SQL query.")

    tables = _load_tables(engagement_id)
    if not tables:
        raise HTTPException(
            status_code=400, detail="No ingested files ready yet — upload and map at least one file first."
        )

    try:
        result = _run_query(tables, sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    csv_bytes = result.write_csv().encode("utf-8")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="query_result.csv"'},
    )
