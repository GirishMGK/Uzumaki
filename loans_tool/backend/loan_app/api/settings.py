"""Settings management endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from fcmr_core.backup import create_backup
from fcmr_core.catalog import store

router = APIRouter()
_templates_dir = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Render settings page."""
    settings = store.list_settings()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"settings": settings, "system_type_map": store.list_system_type_map()},
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
        },
    )
