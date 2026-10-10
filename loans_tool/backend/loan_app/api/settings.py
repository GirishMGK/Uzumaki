"""Settings management endpoints."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from fcmr_core.backup import create_backup
from fcmr_core.catalog import store
from fcmr_core.schema_import import ImportRow, SchemaImportError, parse_mapping_workbook
from fcmr_core.schemas.loader import label_for_report_type

router = APIRouter()
_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

# Parsed-but-not-yet-committed schema imports, keyed by a one-off id handed
# to the preview page and posted back on commit. Deliberately in-memory,
# not persisted -- same rationale as ead_analytics.py's _PROGRESS registry:
# it only needs to survive the one browser tab's review-then-confirm round
# trip, not an app restart. Popped once committed (or just left to be
# replaced/garbage-collected if the admin navigates away without
# confirming -- low-volume, admin-only, not worth a cleanup timer).
_PENDING_IMPORTS: dict[str, list[ImportRow]] = {}


def _column_overrides_context() -> dict:
    overrides = store.list_column_alias_overrides()
    by_report_type: dict[str, list[dict]] = {}
    for ov in overrides:
        by_report_type.setdefault(ov["report_type"], []).append(ov)
    return {
        "column_overrides_by_type": [
            {"report_type": rt, "label": label_for_report_type(rt), "rows": rows}
            for rt, rows in sorted(by_report_type.items())
        ],
    }


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Render settings page."""
    settings = store.list_settings()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "settings": settings,
            "system_type_map": store.list_system_type_map(),
            **_column_overrides_context(),
        },
    )


@router.post("/settings")
async def update_setting(request: Request):
    """Update a setting."""
    form = await request.form()
    key = form.get("key", "").strip()
    value = form.get("value", "").strip()

    if key:
        store.set_setting(key, value)

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "settings": store.list_settings(),
            "system_type_map": store.list_system_type_map(),
            "message": f"✓ Setting '{key}' updated",
            **_column_overrides_context(),
        },
    )


@router.post("/settings/backup")
async def backup_data(request: Request):
    """Create and download a backup of catalog + outputs."""
    try:
        backup_path = create_backup()
        return FileResponse(
            backup_path,
            media_type="application/zip",
            filename=backup_path.name,
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "settings": store.list_settings(),
                "system_type_map": store.list_system_type_map(),
                "message": f"✗ Backup failed: {exc}",
                **_column_overrides_context(),
            },
            status_code=500,
        )


@router.post("/settings/system-types")
async def add_system_type(request: Request):
    """Add or update one System -> Product Type mapping (see
    fcmr_core.catalog.store._DEFAULT_SYSTEM_TYPE_MAP for the seeded
    defaults). Used both from this page directly and as the destination
    an upload's "unmapped System values" notice links to.
    """
    form = await request.form()
    system_value = form.get("system_value", "").strip()
    type_value = form.get("type_value", "").strip()

    message = None
    if system_value and type_value:
        store.set_system_type(system_value, type_value)
        message = f"✓ Mapped '{system_value}' → '{type_value}'"

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "settings": store.list_settings(),
            "system_type_map": store.list_system_type_map(),
            "message": message,
            **_column_overrides_context(),
        },
    )


@router.post("/settings/schema-overrides/preview", response_class=HTMLResponse)
async def schema_overrides_preview(request: Request, file: UploadFile = File(...)):
    """Parse an uploaded Excel sheet (DataSet/Nature/Include/Source
    Column/Output Name/Type/Nullable/Date Format -- the same layout the
    client's own master schema file uses) into a reviewable diff against
    each resolved report type's current schema, without writing anything
    yet. The admin confirms what to actually add on the page this
    returns, which posts to /settings/schema-overrides/commit.
    """
    try:
        rows = parse_mapping_workbook(file.file)
    except SchemaImportError as exc:
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "settings": store.list_settings(),
                "system_type_map": store.list_system_type_map(),
                "message": f"✗ Could not read '{file.filename}': {exc}",
                **_column_overrides_context(),
            },
            status_code=400,
        )

    import_id = str(uuid.uuid4())
    _PENDING_IMPORTS[import_id] = rows

    actionable = [r for r in rows if r.status in ("new_alias", "new_field")]
    already_known = [r for r in rows if r.status == "already_known"]
    unresolved = [r for r in rows if r.status == "unresolved_nature"]
    unresolved_natures = sorted({r.nature for r in unresolved})

    return templates.TemplateResponse(
        request=request,
        name="schema_overrides_preview.html",
        context={
            "import_id": import_id,
            "filename": file.filename,
            "actionable": actionable,
            "already_known_count": len(already_known),
            "unresolved_natures": unresolved_natures,
            "unresolved_count": len(unresolved),
            "total_rows": len(rows),
        },
    )


@router.post("/settings/schema-overrides/commit")
async def schema_overrides_commit(request: Request):
    """Persist the selected rows from a previously-previewed import (see
    schema_overrides_preview above) as column_alias_overrides, then send
    the admin back to Settings with a summary message."""
    form = await request.form()
    import_id = form.get("import_id", "")
    rows = _PENDING_IMPORTS.pop(import_id, None)

    if rows is None:
        return RedirectResponse(
            url="/settings#schema-overrides",
            status_code=303,
        )

    included_row_nums = {int(v) for v in form.getlist("row_num")}
    to_insert = [
        {
            "report_type": r.report_type,
            "canonical": r.canonical,
            "alias": r.source_column,
            "is_new_field": r.status == "new_field",
            "dtype": r.dtype,
            "required": r.required,
        }
        for r in rows
        if r.row_num in included_row_nums and r.status in ("new_alias", "new_field")
    ]

    username = request.session.get("username") or "admin"
    inserted = store.add_column_alias_overrides_batch(to_insert, created_by=username)

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "settings": store.list_settings(),
            "system_type_map": store.list_system_type_map(),
            "message": f"✓ Added {inserted} column mapping override(s)",
            **_column_overrides_context(),
        },
    )


@router.post("/settings/schema-overrides/{override_id}/delete")
async def delete_schema_override(request: Request, override_id: str):
    store.delete_column_alias_override(override_id)
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "settings": store.list_settings(),
            "system_type_map": store.list_system_type_map(),
            "message": "✓ Override removed",
            **_column_overrides_context(),
        },
    )
