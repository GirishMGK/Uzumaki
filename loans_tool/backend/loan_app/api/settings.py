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
        context={"settings": settings},
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
        context={"settings": store.list_settings(), "message": f"✓ Setting '{key}' updated"},
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
            context={"settings": store.list_settings(), "message": f"✗ Backup failed: {exc}"},
            status_code=500,
        )
