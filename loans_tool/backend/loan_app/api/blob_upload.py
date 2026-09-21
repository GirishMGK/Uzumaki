"""Vercel Blob client-upload token endpoint.

On Vercel, serverless functions have a hard 4.5 MB body limit.
Large CSV files bypass this by uploading directly from the browser
to Vercel Blob storage. This module provides the server-side token
so the browser can authenticate with Vercel Blob.

Requires BLOB_READ_WRITE_TOKEN environment variable (set in Vercel project settings).
"""

from __future__ import annotations

import csv
import os
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from fcmr_core.catalog import store

router = APIRouter()

BLOB_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
BLOB_API = "https://blob.vercel-storage.com"


def blob_configured() -> bool:
    return bool(BLOB_TOKEN)


@router.get("/api/blob-token")
async def get_blob_token(request: Request, filename: str):
    """Return a short-lived Vercel Blob client-upload token."""
    if not blob_configured():
        return JSONResponse({"configured": False})

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            BLOB_API,
            headers={
                "Authorization": f"Bearer {BLOB_TOKEN}",
                "x-api-version": "7",
                "content-type": "application/json",
            },
            json={
                "type": "blob.generate-client-token",
                "pathname": f"uploads/{uuid.uuid4()}/{filename}",
                "onUploadCompleted": {
                    "callbackUrl": str(request.base_url).rstrip("/") + "/api/blob-noop"
                },
            },
        )
    data = resp.json()
    data["configured"] = True
    return JSONResponse(data)


@router.get("/api/blob-noop")
async def blob_noop():
    """Dummy callback endpoint required by Vercel Blob onUploadCompleted."""
    return JSONResponse({"ok": True})


@router.post("/dashboard/upload-from-blob")
async def upload_from_blob(
    request: Request,
    blob_url: str = Form(...),
    filename: str = Form(...),
    report_type: str = Form(...),
):
    """Register an upload whose file is already in Vercel Blob storage.

    Downloads only the first 8 KB (enough for the header row), stores
    the blob URL as the csv_path, then proceeds to column mapping.
    """
    engagement_id = request.session.get("engagement_id")
    batch_id = str(uuid.uuid4())

    # Fetch just the first 8 KB to sniff headers — avoids pulling the whole file
    async with httpx.AsyncClient() as client:
        head_resp = await client.get(blob_url, headers={"Range": "bytes=0-8191"})
    first_chunk = head_resp.text

    # Parse the first line as CSV to extract headers
    first_line = first_chunk.split("\n")[0]
    try:
        headers = next(csv.reader([first_line]))
    except Exception:
        headers = [col.strip() for col in first_line.split(",")]

    upload_id = store.create_upload(
        report_type, filename, batch_id=batch_id, engagement_id=engagement_id
    )
    # Store the blob URL in the csv_path field so ingest can download it later
    store.set_mapping_pending(upload_id, csv_path=Path(blob_url), sniffed_headers=headers)

    return RedirectResponse(url=f"/dashboard/uploads/{upload_id}/map-columns", status_code=303)
