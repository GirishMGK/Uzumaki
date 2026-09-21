"""Download endpoints for exception CSVs."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from fcmr_core.catalog import store

router = APIRouter()


@router.get("/runs/{run_id}/download/wide")
async def download_wide(run_id: str):
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run.get("wide_csv"):
        raise HTTPException(status_code=404, detail="Wide CSV not available")
    p = Path(run["wide_csv"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(
        path=str(p),
        media_type="text/csv",
        filename=f"{run_id}_exceptions_wide.csv",
    )


@router.get("/runs/{run_id}/download/long")
async def download_long(run_id: str):
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run.get("long_csv"):
        raise HTTPException(status_code=404, detail="Long CSV not available")
    p = Path(run["long_csv"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(
        path=str(p),
        media_type="text/csv",
        filename=f"{run_id}_exceptions_long.csv",
    )
