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

import os
import tempfile
from pathlib import Path

import duckdb
import polars as pl
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from fcmr_core.catalog import store
from fcmr_core.schemas.loader import available_report_types

router = APIRouter()
_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

PREVIEW_ROW_LIMIT = 500
MAX_SAVED_QUERY_NAME_LENGTH = 120


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


def _strip_trailing_semicolon(sql: str) -> str:
    s = sql.rstrip()
    return s[:-1].rstrip() if s.endswith(";") else s


def _run_query_to_csv(tables: dict[str, pl.DataFrame], sql: str, dest: Path) -> None:
    """Stream a query's result straight to a CSV file via DuckDB's own COPY,
    never materializing it as a polars DataFrame at all -- unlike
    `_run_query` (used by the /run preview, which needs the DataFrame for
    its row-capped JSON response), an export just needs the bytes on disk,
    and COPY writes them directly as DuckDB produces each batch."""
    con = duckdb.connect(":memory:")
    try:
        for name, df in tables.items():
            con.register(name, df)
        # A single trailing ';' is valid on its own but not inside the
        # parens COPY wraps it in -- strip it so a query that already
        # worked via _run_query's plain con.execute(sql) (which tolerates
        # one) doesn't break only on export.
        inner_sql = _strip_trailing_semicolon(sql)
        safe_dest = str(dest).replace("'", "''")
        con.execute(f"COPY ({inner_sql}) TO '{safe_dest}' (FORMAT CSV, HEADER)")
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
        context={
            "tables": table_info,
            "default_sql": default_sql,
            "saved_queries": store.list_saved_queries(),
        },
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

    # Writes straight to a temp file and serves it via FileResponse
    # (streamed in chunks by Starlette) rather than building the whole CSV
    # as a Python string, then again as a bytes object, then handing that
    # single in-memory blob to Response() as the entire HTTP body -- for a
    # large result that's the same data held twice in Python memory with
    # nothing sent to the browser until all of it is ready.
    fd, tmp_name = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        _run_query_to_csv(tables, sql, tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FileResponse(
        tmp_path,
        media_type="text/csv",
        filename="query_result.csv",
        background=BackgroundTask(tmp_path.unlink, missing_ok=True),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Saved queries -- "run it successfully once, click to re-run it next time"
# instead of retyping the SQL. Global (not engagement-scoped): see the
# comment on the saved_sql_queries table for why.
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/dashboard/analytics/sql/saved")
async def sql_analytics_save(request: Request):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    sql = str(form.get("sql", "")).strip()
    if not name:
        return JSONResponse({"error": "Enter a name for this analytics."}, status_code=400)
    if len(name) > MAX_SAVED_QUERY_NAME_LENGTH:
        return JSONResponse(
            {"error": f"Name is too long (max {MAX_SAVED_QUERY_NAME_LENGTH} characters)."}, status_code=400
        )
    if not sql:
        return JSONResponse({"error": "No query to save."}, status_code=400)

    created_by = request.session.get("username") or "admin"
    query_id = store.create_saved_query(name, sql, created_by=created_by)
    return JSONResponse({"query_id": query_id, "name": name, "sql_text": sql})


@router.post("/dashboard/analytics/sql/saved/{query_id}/delete")
async def sql_analytics_delete_saved(query_id: str):
    store.delete_saved_query(query_id)
    return JSONResponse({"ok": True})
