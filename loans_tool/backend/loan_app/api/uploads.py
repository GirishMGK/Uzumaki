"""Upload, column-mapping, and ingest endpoints + main dashboard UI."""

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from fcmr_core.catalog import store
from fcmr_core.config import settings
from fcmr_core.ingestion.pipeline import ingest_csv, sniff_headers
from fcmr_core.logging_setup import get_logger
from fcmr_core.schemas.loader import available_report_types, get_canonical_fields, get_schema

logger = get_logger("loan_app.processing")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _header_signature(headers: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(headers), sort_keys=True).encode()).hexdigest()


def _find_matching_pending_uploads(upload: dict, header_signature: str) -> list[dict]:
    """Other mapping_pending uploads in the same engagement with the same
    report_type whose raw headers hash to the same signature -- i.e. the
    identical column layout, so the same mapping applies to them too.
    header_signature hashes the *sorted* header list, so a match here
    guarantees the exact same header strings (order doesn't matter, but
    a differing column would change the hash) -- safe to reuse the same
    {raw_header: canonical} mapping dict on each match's own file.
    """
    candidates = store.list_uploads(engagement_id=upload.get("engagement_id"))
    matches = []
    for u in candidates:
        if u["upload_id"] == upload["upload_id"]:
            continue
        if u["status"] != "mapping_pending" or u["report_type"] != upload["report_type"]:
            continue
        u_headers = json.loads(u["sniffed_headers"] or "[]")
        if _header_signature(u_headers) == header_signature:
            matches.append(u)
    return matches


def _ingest_and_mark_ready(
    upload: dict,
    user_mapping: dict[str, str],
    *,
    save_profile: bool,
    username: str,
) -> None:
    """The actual ingest-one-file work shared by mapping a single upload and
    batch-applying the same mapping to every other file with an identical
    header layout (see _find_matching_pending_uploads)."""
    upload_id = upload["upload_id"]
    csv_path = Path(upload["csv_path"] or "")
    if not csv_path.exists():
        raise HTTPException(
            status_code=500, detail=f"Uploaded CSV file not found on disk for {upload['filename']}."
        )

    result = ingest_csv(csv_path, upload["report_type"], upload_id, user_mapping=user_mapping)

    store.store_upload_data(upload_id, result.parquet_path)
    csv_path.unlink(missing_ok=True)
    try:
        csv_path.parent.rmdir()
    except Exception:
        pass

    store.set_upload_ready(
        upload_id,
        parquet_path=result.parquet_path,
        row_count=result.total_rows,
        column_mapping=user_mapping,
    )

    if save_profile:
        raw_headers = json.loads(upload["sniffed_headers"] or "[]")
        store.save_mapping_profile(
            upload["report_type"],
            _header_signature(raw_headers),
            json.dumps(user_mapping),
            engagement_id=upload.get("engagement_id"),
            created_by=username,
        )


router = APIRouter()
_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Scope uploads to active engagement
    engagement_id = request.session.get("engagement_id")
    uploads = store.list_uploads(engagement_id=engagement_id) if engagement_id else []
    report_types = available_report_types()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"uploads": uploads, "report_types": report_types},
    )


# ---------------------------------------------------------------------------
# Phase 1 — upload the file and redirect to column-mapping page
# ---------------------------------------------------------------------------


@router.get("/upload", response_class=HTMLResponse)
async def upload_form(request: Request):
    report_types = available_report_types()
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={"report_types": report_types},
    )


@router.post("/upload")
async def do_upload(
    request: Request,
    report_type: str = Form(...),
    folder: list[UploadFile] = File(default=[]),
    files: list[UploadFile] = File(default=[]),
):
    import tempfile
    import zipfile

    # Get engagement_id from session
    engagement_id = request.session.get("engagement_id")

    # Filter out empty UploadFile stubs that browsers send for unselected inputs
    folder = [f for f in folder if f.filename]
    files = [f for f in files if f.filename]

    # Collect all files to process
    upload_files = folder if folder else files
    if not upload_files:
        raise HTTPException(status_code=400, detail="No files provided.")

    # Generate one batch_id for this upload request
    batch_id = str(uuid.uuid4())

    # Checkpoint logging: this is the whole point of a real bug report where
    # the upload hung after the browser finished sending, with no error and
    # nothing in any log -- the *next* time that happens, whatever line
    # logged last here is where it actually got stuck (slow/blocked disk
    # I/O from AV scanning a freshly-written large file being the leading
    # suspect, but this pins it down instead of guessing).
    logger.info(
        "Upload request received: %d file(s), report_type=%s, batch_id=%s",
        len(upload_files), report_type, batch_id,
    )

    # Process files from .zip if present
    temp_dir = None
    try:
        processed_files = []

        for file in upload_files:
            if file.filename and file.filename.lower().endswith(".zip"):
                # Unzip and extract CSVs
                logger.info("Reading uploaded zip: %s", file.filename)
                content = await file.read()
                logger.info("Read %s (%d bytes); extracting", file.filename, len(content))
                temp_dir = tempfile.TemporaryDirectory()
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    zf.extractall(temp_dir.name)
                # Collect all CSVs from unzipped directory
                for root, dirs, filenames in os.walk(temp_dir.name):
                    for fname in filenames:
                        if fname.lower().endswith(".csv"):
                            full_path = Path(root) / fname
                            processed_files.append((fname, full_path.read_bytes()))
            elif file.filename and file.filename.lower().endswith(".csv"):
                # Regular CSV file
                logger.info("Reading uploaded file: %s", file.filename)
                content = await file.read()
                logger.info("Read %s (%d bytes)", file.filename, len(content))
                processed_files.append((file.filename, content))

        if not processed_files:
            raise HTTPException(status_code=400, detail="No CSV files found.")

        # Create upload row for each file. One shared DuckDB connection for
        # the whole batch -- each duckdb.connect() has real per-call
        # overhead, and a large batch (tens of files, each needing 2 store
        # calls) opening/closing a fresh connection per call visibly adds
        # up, easily enough to make a big batch feel stuck even though it's
        # still making progress.
        created_uploads = []
        with store.open_connection() as con:
            for filename, content in processed_files:
                # Guard against a filename carrying path separators (e.g. a
                # crafted multipart request, or a zip entry with a "../"
                # style name) turning into a write outside dest_dir below,
                # or just crashing on a missing intermediate directory.
                filename = os.path.basename(filename)
                if len(content) > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail=f"File {filename} exceeds 2 GB limit.")

                # Create upload row with batch_id and engagement_id
                logger.info("Creating upload record for %s", filename)
                upload_id = store.create_upload(
                    report_type,
                    filename,
                    batch_id=batch_id,
                    engagement_id=engagement_id,
                    con=con,
                )

                # Stream write in 256 KB chunks — avoids holding full file in RAM
                dest_dir = settings.uploads_dir / upload_id
                dest_dir.mkdir(parents=True, exist_ok=True)
                csv_path = dest_dir / filename
                logger.info("Writing %s to disk (%d bytes) at %s", filename, len(content), csv_path)
                chunk_size = 256 * 1024
                with csv_path.open("wb") as out:
                    for i in range(0, len(content), chunk_size):
                        out.write(content[i : i + chunk_size])
                logger.info("Finished writing %s to disk", filename)

                # Sniff headers and set mapping_pending
                headers = sniff_headers(csv_path)
                store.set_mapping_pending(upload_id, csv_path=csv_path, sniffed_headers=headers, con=con)
                logger.info("Upload %s (%s) ready for column mapping", upload_id, filename)

                created_uploads.append((upload_id, filename))

        logger.info("Upload request complete: %d file(s) processed", len(created_uploads))
        # Redirect to dashboard (or could show a batch summary page)
        return RedirectResponse(url="/dashboard", status_code=303)

    finally:
        if temp_dir:
            temp_dir.cleanup()


# ---------------------------------------------------------------------------
# Phase 2 — column-mapping UI
# ---------------------------------------------------------------------------


@router.get("/uploads/{upload_id}/map-columns", response_class=HTMLResponse)
async def map_columns_form(request: Request, upload_id: str):
    upload = store.get_upload(upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload["status"] != "mapping_pending":
        return RedirectResponse(url=f"/dashboard/uploads/{upload_id}", status_code=303)

    raw_headers: list[str] = json.loads(upload["sniffed_headers"] or "[]")
    schema = get_schema(upload["report_type"])

    # Compute header signature for profile lookup
    header_signature = _header_signature(raw_headers)

    # Check for saved profile
    engagement_id = upload.get("engagement_id")
    saved_profile = store.find_profile_by_signature(
        upload["report_type"],
        header_signature,
        engagement_id=engagement_id,
    )

    # Other pending uploads with this exact same column layout -- offered
    # as a "map once, apply to all" option instead of repeating this same
    # screen once per file (the real complaint: uploading a folder of many
    # same-format files, then having to click through mapping for each).
    matching_uploads = _find_matching_pending_uploads(upload, header_signature)

    # If profile found, use it; otherwise compute suggestions with scores
    profile_applied = False
    suggested: dict[str, str] = {}
    suggested_with_scores: dict[str, tuple[str, float]] = {}

    if saved_profile:
        suggested = json.loads(saved_profile["mapping_json"])
        profile_applied = True
    else:
        if schema:
            suggested_with_scores = schema.map_headers_with_scores(raw_headers)
            # Build suggested dict from fuzzy+exact matches (keys are raw_headers, values are canonicals)
            suggested = {
                raw_h: canonical for raw_h, (canonical, score) in suggested_with_scores.items()
            }

    canonical_fields = get_canonical_fields(upload["report_type"])

    # Invert suggested map: canonical -> raw_header (for the new UI direction)
    suggested_inverse = {canonical: raw_h for raw_h, canonical in suggested.items()}

    return templates.TemplateResponse(
        request=request,
        name="column_map.html",
        context={
            "upload": upload,
            "raw_headers": raw_headers,
            "suggested": suggested,
            "suggested_with_scores": suggested_with_scores,
            "suggested_inverse": suggested_inverse,
            "canonical_fields": canonical_fields,
            "profile_applied": profile_applied,
            "profile_id": saved_profile.get("profile_id") if saved_profile else None,
            "matching_count": len(matching_uploads),
        },
    )


@router.post("/uploads/{upload_id}/map-columns")
async def do_map_columns(request: Request, upload_id: str):
    upload = store.get_upload(upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    form = await request.form()
    raw_headers: list[str] = json.loads(upload["sniffed_headers"] or "[]")

    # Form fields are named map_<canonical> = raw_header (canonical fields are the fixed left side)
    canonical_fields = get_canonical_fields(upload["report_type"])
    user_mapping: dict[str, str] = {}
    for spec in canonical_fields:
        raw_header = str(form.get(f"map_{spec.canonical}", "") or "").strip()
        if raw_header and raw_header != "__skip__":
            user_mapping[raw_header] = spec.canonical

    username = request.session.get("username", "admin")

    # "Apply to all matching files" -- resolve the other pending uploads
    # with this exact same header layout *before* ingesting the current
    # one, since ingest_csv() deletes the raw CSV (which set_mapping_pending
    # never touches for the others, but the header_signature match itself
    # depends on the current upload's own sniffed_headers still being intact).
    apply_to_matching = form.get("apply_to_matching") == "1"
    matching_uploads = (
        _find_matching_pending_uploads(upload, _header_signature(raw_headers))
        if apply_to_matching
        else []
    )

    try:
        _ingest_and_mark_ready(upload, user_mapping, save_profile=True, username=username)
    except Exception as exc:
        store.set_upload_failed(upload_id, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}") from exc

    if not matching_uploads:
        return RedirectResponse(url=f"/dashboard/uploads/{upload_id}", status_code=303)

    # Batch-apply: ingest every matching file the same way. One file
    # failing (e.g. a subtly different row layout despite identical
    # headers) marks that upload failed and moves on -- it shouldn't block
    # the rest of an otherwise-successful batch.
    for m in matching_uploads:
        try:
            _ingest_and_mark_ready(m, user_mapping, save_profile=False, username=username)
        except Exception as exc:
            store.set_upload_failed(m["upload_id"], error=str(exc))
            logger.error("Batch mapping failed for %s: %s", m["filename"], exc)

    logger.info(
        "Batch-applied mapping from %s to %d matching file(s)",
        upload["filename"], len(matching_uploads),
    )
    return RedirectResponse(url="/dashboard", status_code=303)


# ---------------------------------------------------------------------------
# Upload detail
# ---------------------------------------------------------------------------


@router.get("/uploads/{upload_id}", response_class=HTMLResponse)
async def upload_detail(request: Request, upload_id: str):
    from fcmr_core.rules.registry import list_categories

    upload = store.get_upload(upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    mapping_display: list[tuple[str, str]] = []
    if upload.get("column_mapping"):
        mapping_display = list(json.loads(upload["column_mapping"]).items())

    runs = store.list_runs(upload_id)
    categories = list_categories()
    return templates.TemplateResponse(
        request=request,
        name="upload_detail.html",
        context={
            "upload": upload,
            "runs": runs,
            "mapping_display": mapping_display,
            "categories": categories,
        },
    )


@router.post("/uploads/{upload_id}/delete")
async def delete_upload(upload_id: str):
    upload = store.get_upload(upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    store.delete_upload(upload_id)
    return RedirectResponse(url="/dashboard", status_code=303)
