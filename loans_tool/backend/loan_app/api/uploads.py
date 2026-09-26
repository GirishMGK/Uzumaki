"""Upload, column-mapping, and ingest endpoints + main dashboard UI."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

import duckdb
import polars as pl

from fcmr_core.catalog import store
from fcmr_core.config import settings
from fcmr_core.ingestion.pipeline import ingest_csv, sniff_headers
from fcmr_core.logging_setup import get_logger
from fcmr_core.schemas.loader import (
    available_report_types,
    best_raw_for_canonical_from_scores,
    get_canonical_fields,
    get_schema,
    label_for_report_type,
)

logger = get_logger("loan_app.processing")

# Canonical field that feeds the Product Helper tag (see _tag_product_helper
# and Settings -> System -> Product Type Mapping). Any report type whose
# schema maps a raw header to this canonical gets checked/tagged.
_SYSTEM_CANONICAL = "system"


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


def _distinct_raw_values(csv_path: Path, raw_header: str) -> list[str]:
    """Distinct non-null values of one raw CSV column, read directly (the
    file is still just mapping_pending at this point, no need to wait for
    the full ingest to inspect it)."""
    safe_header = raw_header.replace('"', '""')
    with duckdb.connect() as con:
        rows = con.execute(
            f"""
            SELECT DISTINCT "{safe_header}" FROM read_csv(
                ?, auto_detect=true, ignore_errors=true, sample_size=10000, strict_mode=false
            ) WHERE "{safe_header}" IS NOT NULL
            """,
            [str(csv_path)],
        ).fetchall()
    return [r[0] for r in rows]


def _unmapped_system_values(upload: dict, user_mapping: dict[str, str]) -> list[str]:
    """If this upload's mapping includes the `system` canonical field,
    return whichever of its distinct raw values aren't in the persisted
    System -> Product Type lookup yet (see Settings). Empty list if
    `system` isn't mapped, or everything already resolves."""
    raw_header = next((h for h, c in user_mapping.items() if c == _SYSTEM_CANONICAL), None)
    if not raw_header:
        return []
    csv_path = Path(upload["csv_path"] or "")
    if not csv_path.exists():
        return []
    known = store.get_system_type_map()
    values = _distinct_raw_values(csv_path, raw_header)
    return sorted({v for v in values if v not in known})


_SUPPORTED_UPLOAD_EXTENSIONS = (".csv", ".xlsx", ".xls", ".parquet")


def _as_csv_bytes(filename: str, content: bytes) -> tuple[str, bytes] | None:
    """Normalise one uploaded file to (csv_filename, csv_bytes) so the rest
    of the pipeline (sniff_headers, ingest_csv, ...) only ever deals with
    CSV -- Excel and Parquet are just alternate source formats for the
    exact same EAD-style data, converted here rather than teaching every
    downstream step three different formats. Returns None for anything
    else (caller skips it)."""
    lower = filename.lower()
    if lower.endswith(".csv"):
        return filename, content
    if lower.endswith((".xlsx", ".xls")):
        df = pl.read_excel(io.BytesIO(content), engine="calamine")
    elif lower.endswith(".parquet"):
        df = pl.read_parquet(io.BytesIO(content))
    else:
        return None
    csv_name = str(Path(filename).with_suffix(".csv"))
    return csv_name, df.write_csv().encode("utf-8")


def _tag_product_helper(parquet_path: Path) -> None:
    """If this dataset has a `system` column (from the canonical mapping
    above), add a `product_helper` column tagging each row with its
    Product Type via the persisted System -> Type lookup. A value with no
    match is left null rather than failing the file -- for a single
    upload that's already impossible (do_map_columns blocks on any
    unmapped value first), but a secondary file batch-applied via "apply
    to matching" isn't re-checked individually, so it can still happen
    there.
    """
    df = pl.read_parquet(parquet_path)
    if _SYSTEM_CANONICAL not in df.columns:
        return
    mapping = store.get_system_type_map()
    df = df.with_columns(
        pl.col(_SYSTEM_CANONICAL).replace_strict(mapping, default=None).alias("product_helper")
    )
    df.write_parquet(parquet_path)


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
    _tag_product_helper(result.parquet_path)

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


@router.get("/schema/{report_type}/download")
async def download_schema(report_type: str):
    """A report type's canonical schema as a two-sheet Excel workbook: a
    blank Template (headers are the exact canonical field names -- a file
    built from this needs no column mapping at all, every column
    auto-matches on upload) and a Field Reference (required?, data type,
    and every raw column name this schema already recognizes automatically
    -- so keeping one of *those* names instead of matching the Template
    exactly also auto-maps). Meant to be downloaded before preparing a
    source file, so the mapping step on upload is a formality rather than
    a manual per-column exercise.
    """
    if report_type not in available_report_types():
        raise HTTPException(status_code=404, detail=f"Unknown report type: {report_type}")

    fields = get_canonical_fields(report_type)
    label = label_for_report_type(report_type)

    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    header_fill = PatternFill(start_color="1B3A5C", end_color="1B3A5C", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=10)

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Template"
    for ci, spec in enumerate(fields, start=1):
        cell = ws1.cell(row=1, column=ci, value=spec.canonical)
        cell.fill = header_fill
        cell.font = header_font
        ws1.column_dimensions[get_column_letter(ci)].width = max(len(spec.canonical) + 2, 14)

    ws2 = wb.create_sheet("Field Reference")
    for ci, h in enumerate(["Canonical Field", "Required", "Data Type", "Accepted Column Names"], start=1):
        cell = ws2.cell(row=1, column=ci, value=h)
        cell.fill = header_fill
        cell.font = header_font
    for ri, spec in enumerate(fields, start=2):
        ws2.cell(row=ri, column=1, value=spec.canonical)
        ws2.cell(row=ri, column=2, value="Yes" if spec.required else "No")
        ws2.cell(row=ri, column=3, value=spec.dtype)
        ws2.cell(row=ri, column=4, value=", ".join(spec.aliases))
    for ci, width in enumerate([24, 12, 12, 70], start=1):
        ws2.column_dimensions[get_column_letter(ci)].width = width
    ws2.freeze_panes = "A2"

    fd, tmp_name = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    tmp_path = Path(tmp_name)
    wb.save(tmp_path)

    filename = f"{label.replace(' ', '_')}_Schema.xlsx"
    return FileResponse(
        tmp_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
        background=BackgroundTask(tmp_path.unlink, missing_ok=True),
    )


def _check_size(size: int, filename: str) -> None:
    if size > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"File {filename} exceeds 5 GB limit.")


async def _stream_to_path(file: UploadFile, dest: Path, chunk_size: int = 256 * 1024) -> int:
    """Copy an UploadFile straight to disk in fixed-size chunks, checking
    the running total against the upload cap as data arrives. Never holds
    more than one chunk of the file in memory at a time -- unlike a plain
    `await file.read()` (the whole file, up to the 5 GB cap, as one Python
    bytes object) followed by a chunked *re*-write of that same in-memory
    buffer, which is what this replaces. Returns the total bytes written.
    """
    total = 0
    with dest.open("wb") as out:
        while chunk := await file.read(chunk_size):
            total += len(chunk)
            _check_size(total, file.filename or dest.name)
            out.write(chunk)
    return total


@router.post("/upload")
async def do_upload(
    request: Request,
    report_type: str = Form(...),
    folder: list[UploadFile] = File(default=[]),
    files: list[UploadFile] = File(default=[]),
):
    import shutil
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
        # (filename, source) queued for the write phase below. `source` is
        # either a Path to a file already sitting on disk to be moved into
        # place (a streamed direct CSV upload, or a CSV extracted from a
        # zip -- neither ever needs to be read into Python memory at all),
        # or `bytes` already fully materialized (an Excel/Parquet file
        # converted to CSV -- both formats need their whole file available
        # to parse, so there's no streaming option for them regardless of
        # source).
        processed_files: list[tuple[str, Path | bytes]] = []

        def _ensure_temp_dir() -> tempfile.TemporaryDirectory:
            nonlocal temp_dir
            if temp_dir is None:
                temp_dir = tempfile.TemporaryDirectory()
            return temp_dir

        for file in upload_files:
            fname_lower = (file.filename or "").lower()

            if fname_lower.endswith(".zip"):
                # Stream the zip itself straight to disk rather than
                # buffering potentially several GB of zip bytes in memory
                # just to hand them to zipfile.ZipFile -- it opens just as
                # well from a path as from BytesIO.
                logger.info("Streaming uploaded zip to disk: %s", file.filename)
                td = _ensure_temp_dir()
                zip_path = Path(td.name) / f"{uuid.uuid4()}.zip"
                total = await _stream_to_path(file, zip_path)
                logger.info("Streamed %s (%d bytes); extracting", file.filename, total)

                extract_dir = Path(td.name) / f"extracted_{uuid.uuid4()}"
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(extract_dir)
                zip_path.unlink(missing_ok=True)

                for root, dirs, filenames in os.walk(extract_dir):
                    for fname in filenames:
                        if not fname.lower().endswith(_SUPPORTED_UPLOAD_EXTENSIONS):
                            continue
                        full_path = Path(root) / fname
                        if fname.lower().endswith(".csv"):
                            # Already on disk in the format we want --
                            # queue it to be moved into place directly,
                            # never read fully into memory.
                            _check_size(full_path.stat().st_size, fname)
                            processed_files.append((fname, full_path))
                        else:
                            raw = full_path.read_bytes()
                            _check_size(len(raw), fname)
                            converted = _as_csv_bytes(fname, raw)
                            if converted:
                                _check_size(len(converted[1]), fname)
                                processed_files.append(converted)

            elif fname_lower.endswith(".csv"):
                # Stream straight to a staging path -- never materialize
                # the whole file in a Python bytes object. Moved into its
                # final upload_id-keyed location in the write phase below.
                logger.info("Streaming uploaded file to disk: %s", file.filename)
                td = _ensure_temp_dir()
                staged_path = Path(td.name) / f"{uuid.uuid4()}.csv"
                total = await _stream_to_path(file, staged_path)
                logger.info("Streamed %s (%d bytes)", file.filename, total)
                processed_files.append((file.filename, staged_path))

            elif fname_lower.endswith(_SUPPORTED_UPLOAD_EXTENSIONS):
                # Excel/Parquet: no streaming path -- polars needs the
                # whole file available to parse either format's structure.
                logger.info("Reading uploaded file: %s", file.filename)
                content = await file.read()
                logger.info("Read %s (%d bytes)", file.filename, len(content))
                _check_size(len(content), file.filename)
                converted = _as_csv_bytes(file.filename, content)
                if converted:
                    _check_size(len(converted[1]), file.filename)
                    processed_files.append(converted)

        if not processed_files:
            raise HTTPException(
                status_code=400, detail="No CSV, Excel, or Parquet files found."
            )

        # Create upload row for each file. One shared DuckDB connection for
        # the whole batch -- each duckdb.connect() has real per-call
        # overhead, and a large batch (tens of files, each needing 2 store
        # calls) opening/closing a fresh connection per call visibly adds
        # up, easily enough to make a big batch feel stuck even though it's
        # still making progress.
        created_uploads = []
        with store.open_connection() as con:
            for filename, source in processed_files:
                # Guard against a filename carrying path separators (e.g. a
                # crafted multipart request, or a zip entry with a "../"
                # style name) turning into a write outside dest_dir below,
                # or just crashing on a missing intermediate directory.
                filename = os.path.basename(filename)

                # Create upload row with batch_id and engagement_id
                logger.info("Creating upload record for %s", filename)
                upload_id = store.create_upload(
                    report_type,
                    filename,
                    batch_id=batch_id,
                    engagement_id=engagement_id,
                    con=con,
                )

                dest_dir = settings.uploads_dir / upload_id
                dest_dir.mkdir(parents=True, exist_ok=True)
                csv_path = dest_dir / filename

                if isinstance(source, Path):
                    logger.info("Moving %s into place at %s", filename, csv_path)
                    shutil.move(str(source), str(csv_path))
                else:
                    logger.info("Writing %s to disk (%d bytes) at %s", filename, len(source), csv_path)
                    with csv_path.open("wb") as out:
                        out.write(source)
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

    # canonical -> raw_header, for the UI's per-canonical dropdown default.
    # Not a naive inversion of `suggested`: when two raw headers both
    # fuzzy-match the same canonical (e.g. a real file's own "_Hist"/"_Old"
    # variant of an exact-match column), best_raw_for_canonical_from_scores()
    # keeps the higher-scoring one rather than whichever was seen last --
    # otherwise the exact match gets left unmapped while the fuzzy variant
    # is suggested in its place, and confirming that mapping later corrupts
    # ingestion (two columns colliding on the same name). Built from the
    # `suggested_with_scores` already computed above rather than calling
    # schema.best_raw_for_canonical(raw_headers) (which would re-run the
    # same fuzzy-match scoring pass, and its DB round trip for the
    # threshold setting, a second time for no reason).
    if saved_profile:
        suggested_inverse = {canonical: raw_h for raw_h, canonical in suggested.items()}
    elif schema:
        suggested_inverse = best_raw_for_canonical_from_scores(suggested_with_scores)
    else:
        suggested_inverse = {}

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

    # Block on any System value this file has that isn't in the persisted
    # System -> Product Type lookup yet, rather than silently tagging those
    # rows with a null Product Helper -- add the mapping at Settings, then
    # re-open this same mapping screen and confirm again.
    unmapped_systems = _unmapped_system_values(upload, user_mapping)
    if unmapped_systems:
        return templates.TemplateResponse(
            request=request,
            name="unmapped_systems.html",
            context={"upload": upload, "unmapped_systems": unmapped_systems},
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


@router.post("/uploads/bulk-delete")
async def bulk_delete_uploads(request: Request):
    """Delete many uploads in one action -- the real scenario this exists
    for: a batch upload that looked stuck (see the checkpoint-logging
    fix) got retried several times before the user realized each attempt
    had actually succeeded, leaving many duplicate mapping_pending rows
    that would be painful to remove one Delete click at a time.
    """
    form = await request.form()
    upload_ids = [str(v) for v in form.getlist("upload_ids")]
    for upload_id in upload_ids:
        if store.get_upload(upload_id):
            store.delete_upload(upload_id)
    return RedirectResponse(url="/dashboard", status_code=303)
